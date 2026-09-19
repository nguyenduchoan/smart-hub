"""Appliances, IR actions, remote learning, and observations endpoints."""
import base64
from datetime import datetime
import os
import threading
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...devices import (
    Appliance,
    ApplianceCategory,
    CodeRevision,
    CommandState,
    DeviceStorage,
    Observation,
    ObservationOutcome,
)
from ...devices.catalogs.importer import validate_broadlink_payload
from ...devices.providers.broadlink_provider import BroadlinkProvider
from ...devices.providers.mock_provider import MockDeviceProvider
from ...locks import gateway_lock, ResourceBusyError

router = APIRouter(prefix="/api", tags=["Devices"])

# In-memory dictionary of active learning jobs
LEARNING_JOBS: Dict[str, Dict[str, Any]] = {}


def get_provider():
    if os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1":
        return MockDeviceProvider()
    provider = BroadlinkProvider()
    if not provider.is_available():
        return MockDeviceProvider()
    return provider


class CreateApplianceRequest(BaseModel):
    name: str = Field(..., description="Tên thiết bị, e.g. Điều hòa Daikin Phòng Ngủ")
    room: str = Field(default="", description="Phòng, e.g. Phòng ngủ")
    category: ApplianceCategory = Field(..., description="tv, fan, climate, custom")
    brand: str = Field(..., description="Hãng sản xuất")
    model: str = Field(default="Universal", description="Mã model thiết bị")
    gateway_id: str = Field(..., description="ID của Broadlink RM4 gateway điều khiển")
    code_set_id: Optional[str] = Field(default=None, description="ID của bộ mã catalog nếu chọn từ catalog")


class SendActionRequest(BaseModel):
    request_id: str = Field(..., description="Định danh idempotency duy nhất cho mỗi lần bấm")
    button_key: str = Field(..., description="Khóa nút, e.g. power_toggle, cool_auto_26c")
    code_revision_id: Optional[str] = Field(default=None, description="ID phiên bản mã; nếu trống dùng active")


class StartLearningRequest(BaseModel):
    button_key: str = Field(..., description="Khóa nút cần học, e.g. power_toggle, speed_up")
    button_name: str = Field(..., description="Tên hiển thị của nút, e.g. Bật/Tắt, Tăng tốc độ")
    timeout_seconds: float = 30.0


class ObservationRequest(BaseModel):
    outcome: ObservationOutcome = Field(..., description="accurate (đúng chức năng), inaccurate (sai), unknown (chưa rõ)")
    user_notes: str = Field(default="", description="Ghi chú quan sát")


@router.get("/devices")
def list_appliances(gateway_id: Optional[str] = None):
    storage = DeviceStorage()
    appliances = storage.list_appliances(gateway_id=gateway_id)
    result = []
    for app in appliances:
        revisions = storage.list_code_revisions(appliance_id=app.id)
        gw = storage.get_gateway(app.gateway_id)
        app_dict = app.to_dict()
        app_dict["buttons_count"] = len({r.button_key for r in revisions})
        app_dict["gateway_name"] = gw.name if gw else "Không rõ"
        result.append(app_dict)
    return result


@router.post("/devices")
def create_appliance(req: CreateApplianceRequest):
    storage = DeviceStorage()
    gw = storage.get_gateway(req.gateway_id)
    if not gw:
        raise HTTPException(status_code=404, detail=f"Gateway '{req.gateway_id}' không tồn tại.")

    app_id = f"dev_{uuid.uuid4().hex[:10]}"
    now = datetime.now().astimezone().isoformat()
    app = Appliance(
        id=app_id,
        name=req.name,
        room=req.room,
        category=req.category,
        brand=req.brand,
        model=req.model,
        gateway_id=req.gateway_id,
        code_set_id=req.code_set_id,
        mapping_revision=1,
        created_at=now,
        updated_at=now,
    )
    storage.save_appliance(app)

    # If code_set_id was chosen, populate initial code revisions from catalog
    if req.code_set_id:
        cs = storage.get_code_set(req.code_set_id)
        if cs:
            for btn_key, b64_code in cs.codes.items():
                rev_id = f"rev_{uuid.uuid4().hex[:10]}"
                payload_bytes = base64.b64decode(b64_code)
                rev = CodeRevision(
                    id=rev_id,
                    code_set_id=cs.id,
                    appliance_id=app.id,
                    button_key=btn_key,
                    button_name=btn_key.replace("_", " ").title(),
                    payload_base64=b64_code,
                    payload_hash=CodeRevision.compute_hash(payload_bytes),
                    source_type="catalog",
                    revision_number=1,
                    is_verified=False,
                    created_at=now,
                )
                storage.save_code_revision(rev)

    return app.to_dict()


@router.get("/devices/{device_id}")
def get_appliance_detail(device_id: str):
    storage = DeviceStorage()
    app = storage.get_appliance(device_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"Thiết bị '{device_id}' không tồn tại.")

    revisions = storage.list_code_revisions(appliance_id=app.id)
    observations = storage.list_observations(appliance_id=app.id)
    gw = storage.get_gateway(app.gateway_id)

    # Group revisions by button_key, taking the latest revision
    buttons_map: Dict[str, Any] = {}
    for r in revisions:
        if r.button_key not in buttons_map or r.revision_number > buttons_map[r.button_key]["revision_number"]:
            buttons_map[r.button_key] = r.to_dict()

    data = app.to_dict()
    data["gateway"] = gw.to_dict() if gw else None
    data["buttons"] = list(buttons_map.values())
    data["recent_observations"] = [o.to_dict() for o in observations[:10]]
    return data


@router.delete("/devices/{device_id}")
def delete_appliance(device_id: str):
    storage = DeviceStorage()
    deleted = storage.delete_appliance(device_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Thiết bị '{device_id}' không tồn tại.")
    return {"status": "ok", "deleted": device_id}


@router.post("/devices/{device_id}/actions")
def send_action(device_id: str, req: SendActionRequest):
    storage = DeviceStorage()
    app = storage.get_appliance(device_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"Thiết bị '{device_id}' không tồn tại.")

    gw = storage.get_gateway(app.gateway_id)
    if not gw:
        raise HTTPException(status_code=404, detail=f"Gateway '{app.gateway_id}' không tồn tại.")

    # Idempotency check: if request_id already exists in ledger, return existing state
    existing_entry = storage.get_ledger_entry(req.request_id)
    if existing_entry:
        return {
            "request_id": existing_entry.request_id,
            "state": existing_entry.state.value,
            "gateway_ack": existing_entry.state == CommandState.DELIVERED,
            "message": "Lệnh đã được gửi trước đó (trả kết quả từ ledger).",
            "sent_at": existing_entry.sent_at,
        }

    # Resolve code revision
    if req.code_revision_id:
        rev = storage.get_code_revision(req.code_revision_id)
    else:
        rev = storage.get_active_code_revision(app.id, req.button_key)

    if not rev:
        raise HTTPException(
            status_code=400,
            detail=f"Nút '{req.button_key}' chưa có mã IR. Vui lòng chọn catalog hoặc học từ remote gốc.",
        )

    # Validate payload
    try:
        code_bytes = validate_broadlink_payload(rev.payload_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Mã IR không hợp lệ: {exc}")

    # Prepare command ledger
    storage.prepare_command(
        request_id=req.request_id,
        gateway_id=gw.id,
        appliance_id=app.id,
        button_key=req.button_key,
        code_revision_id=rev.id,
    )
    storage.update_command_state(req.request_id, CommandState.DISPATCHING)

    # Acquire gateway lock and dispatch IR
    provider = get_provider()
    try:
        with gateway_lock(gw.id, timeout=2.0):
            provider.send_code(gw, code_bytes)
            storage.update_command_state(
                req.request_id,
                CommandState.DELIVERED,
                raw_ack="ACK_RECEIVED",
            )
            return {
                "request_id": req.request_id,
                "state": CommandState.DELIVERED.value,
                "gateway_ack": True,
                "message": f"Đã gửi mã tới {gw.name} (nhận ACK). Vui lòng quan sát thiết bị.",
                "button_key": req.button_key,
                "code_revision_id": rev.id,
            }
    except ResourceBusyError:
        storage.update_command_state(
            req.request_id,
            CommandState.FAILED,
            error_message="Gateway đang bận xử lý tác vụ khác",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gateway đang bận. Vui lòng thử lại sau giây lát.",
        )
    except Exception as exc:
        storage.update_command_state(
            req.request_id,
            CommandState.UNKNOWN,
            error_message=str(exc),
        )
        return {
            "request_id": req.request_id,
            "state": CommandState.UNKNOWN.value,
            "gateway_ack": False,
            "message": f"Chưa rõ kết quả gửi: {exc}. Vui lòng quan sát thiết bị trước khi thử lại.",
            "button_key": req.button_key,
            "code_revision_id": rev.id,
        }


# --- IR Learning ---

@router.post("/devices/{device_id}/learning-jobs")
def start_learning_job(device_id: str, req: StartLearningRequest):
    storage = DeviceStorage()
    app = storage.get_appliance(device_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"Thiết bị '{device_id}' không tồn tại.")

    gw = storage.get_gateway(app.gateway_id)
    if not gw:
        raise HTTPException(status_code=404, detail=f"Gateway '{app.gateway_id}' không tồn tại.")

    job_id = f"learn_{uuid.uuid4().hex[:10]}"
    cancel_token = threading.Event()

    job_entry = {
        "job_id": job_id,
        "device_id": device_id,
        "gateway_id": gw.id,
        "button_key": req.button_key,
        "button_name": req.button_name,
        "status": "running",
        "progress_message": "Đang vào chế độ học IR; hướng remote gốc vào RM4 và bấm 1 nút...",
        "cancel_token": cancel_token,
        "code_revision": None,
        "error": None,
        "started_at": datetime.now().astimezone().isoformat(),
    }
    LEARNING_JOBS[job_id] = job_entry

    def worker():
        provider = get_provider()
        try:
            with gateway_lock(gw.id, timeout=0.0):
                raw_ir = provider.enter_learning(
                    gw,
                    timeout=req.timeout_seconds,
                    cancel_token=cancel_token,
                )
                b64_code = base64.b64encode(raw_ir).decode("ascii")
                rev_hash = CodeRevision.compute_hash(raw_ir)

                # Fetch active revision to increment revision_number
                prev_rev = storage.get_active_code_revision(app.id, req.button_key)
                next_rev_num = (prev_rev.revision_number + 1) if prev_rev else 1

                rev_id = f"rev_{uuid.uuid4().hex[:10]}"
                now_str = datetime.now().astimezone().isoformat()
                rev = CodeRevision(
                    id=rev_id,
                    code_set_id=None,
                    appliance_id=app.id,
                    button_key=req.button_key,
                    button_name=req.button_name,
                    payload_base64=b64_code,
                    payload_hash=rev_hash,
                    source_type="learned",
                    revision_number=next_rev_num,
                    is_verified=False,
                    created_at=now_str,
                )
                storage.save_code_revision(rev)

                job_entry["status"] = "completed"
                job_entry["code_revision"] = rev.to_dict()
                job_entry["progress_message"] = "Đã nhận mã IR thành công! Vui lòng gửi thử để kiểm tra."
        except ResourceBusyError:
            job_entry["status"] = "failed"
            job_entry["error"] = "Gateway đang bận, không thể bắt đầu học mã."
        except InterruptedError:
            job_entry["status"] = "cancelled"
            job_entry["progress_message"] = "Đã hủy học mã theo yêu cầu."
        except Exception as exc:
            job_entry["status"] = "failed"
            job_entry["error"] = str(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    return {
        "job_id": job_id,
        "status": "running",
        "message": "Đã bắt đầu học IR; hướng remote vào RM4.",
    }


@router.get("/devices/{device_id}/learning-jobs/{job_id}")
def get_learning_job_status(device_id: str, job_id: str):
    job = LEARNING_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy learning job.")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "message": job.get("progress_message") or job.get("error"),
        "code_revision": job.get("code_revision"),
        "error": job.get("error"),
    }


@router.post("/devices/{device_id}/learning-jobs/{job_id}/cancel")
def cancel_learning_job(device_id: str, job_id: str):
    job = LEARNING_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy learning job.")
    cancel_token = job.get("cancel_token")
    if cancel_token:
        cancel_token.set()
    job["status"] = "cancelling"
    return {"status": "cancelling", "job_id": job_id}


# --- Observations ---

@router.post("/code-revisions/{revision_id}/observations")
def record_observation(revision_id: str, req: ObservationRequest):
    storage = DeviceStorage()
    rev = storage.get_code_revision(revision_id)
    if not rev:
        raise HTTPException(status_code=404, detail=f"CodeRevision '{revision_id}' không tồn tại.")

    obs_id = f"obs_{uuid.uuid4().hex[:10]}"
    obs = Observation(
        id=obs_id,
        appliance_id=rev.appliance_id or "",
        code_revision_id=rev.id,
        button_key=rev.button_key,
        outcome=req.outcome,
        user_notes=req.user_notes,
        recorded_at=datetime.now().astimezone().isoformat(),
    )
    storage.record_observation(obs)
    return obs.to_dict()
