"""Audio recording session control endpoints for child and adult voice study."""
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from ...child_study import NEGATIVE_PRESETS
from ...recording import RecordingService, RecordingState, SessionConfig

router = APIRouter(prefix="/api/recording", tags=["Recording"])

# Global singleton recording service for web dashboard instance
RECORDING_SERVICE = RecordingService()


class StartRecordingRequest(BaseModel):
    speaker: str = Field(..., description="'adult' hoặc 'child'")
    speaker_id: str = Field(..., description="Mã người nói, e.g. child_01, adult_01")
    split: str = Field(default="pilot", description="'pilot', 'dev', 'test'")
    phrase: Optional[str] = Field(default=None, description="Câu cần nói")
    preset: Optional[int] = Field(default=None, description="Mã preset câu âm tính (1-10)")
    label: Optional[str] = Field(default=None, description="'positive' hoặc 'negative'")
    distance_m: float = Field(default=1.0, gt=0.0, le=50.0, description="Khoảng cách tới mic (mét, > 0 và <= 50)")
    condition: str = Field(default="quiet_normal_voice", description="Điều kiện âm thanh")
    takes_planned: int = Field(default=5, gt=0, le=100, description="Số lượt thu (> 0 và <= 100)")
    manual_advance: bool = Field(default=True, description="Chờ người dùng bấm trước từng lượt")
    session_id: Optional[str] = Field(default=None, description="Mã phiên tùy chọn")
    mock: bool = Field(default=False, description="Chạy giả lập không mở microphone")

    @field_validator("speaker")
    @classmethod
    def validate_speaker(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if v_clean not in ("adult", "child"):
            raise ValueError(f"speaker phải là 'adult' hoặc 'child', nhận '{v}'")
        return v_clean

    @field_validator("speaker_id")
    @classmethod
    def validate_speaker_id(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("speaker_id không được để trống")
        return v_clean

    @field_validator("split")
    @classmethod
    def validate_split(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if v_clean not in ("pilot", "dev", "test"):
            raise ValueError(f"split phải là 'pilot', 'dev', hoặc 'test', nhận '{v}'")
        return v_clean

    @field_validator("label")
    @classmethod
    def validate_label(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v_clean = v.strip().lower()
        if v_clean not in ("positive", "negative"):
            raise ValueError(f"label phải là 'positive' hoặc 'negative', nhận '{v}'")
        return v_clean

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("condition không được để trống")
        return v_clean


class AdvanceTakeRequest(BaseModel):
    session_id: str = Field(..., description="Mã phiên thu cần advance")
    take_sequence: int = Field(..., description="Số thứ tự lượt thu dự kiến bắt đầu")

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("session_id bắt buộc và không được để trống")
        return v.strip()

    @field_validator("take_sequence", mode="before")
    @classmethod
    def validate_take_sequence(cls, v: Any) -> int:
        if type(v) is not int or isinstance(v, bool) or v <= 0:
            raise ValueError("take_sequence phải là số nguyên dương (> 0) và không nhận chuỗi, float hoặc boolean")
        return v


@router.get("/status")
def get_recording_status():
    return RECORDING_SERVICE.get_status()


@router.post("/start")
def start_recording(req: StartRecordingRequest):
    # Validate phrase and label contracts
    if req.preset is not None:
        if req.preset not in NEGATIVE_PRESETS:
            raise HTTPException(status_code=400, detail=f"Preset {req.preset} không hợp lệ (1-10).")
        phrase = NEGATIVE_PRESETS[req.preset]
        label = "negative"
        if req.label == "positive":
            raise HTTPException(status_code=400, detail="--preset chỉ áp dụng cho câu âm tính (negative).")
    elif req.phrase:
        phrase = req.phrase.strip()
        label = req.label or "positive"
    else:
        phrase = "Maika ơi"
        label = req.label or "positive"

    expected_events = 1 if label == "positive" else 0

    session_cfg = SessionConfig(
        speaker=req.speaker,
        speaker_id=req.speaker_id,
        split=req.split,
        label=label,
        phrase=phrase,
        expected_events=expected_events,
        distance_m=req.distance_m,
        condition=req.condition,
        takes_planned=req.takes_planned,
        manual_advance=req.manual_advance,
        session_id=req.session_id,
        no_sync=False,
        mock=req.mock,
    )

    try:
        RECORDING_SERVICE.start_session(session_cfg)
        return {"status": "ok", "message": "Đã bắt đầu phiên thu âm.", "session_status": RECORDING_SERVICE.get_status()}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/advance")
def advance_take(req: AdvanceTakeRequest):
    if RECORDING_SERVICE.state != RecordingState.WAITING_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Chỉ có thể bấm lượt tiếp theo khi trạng thái là 'waiting_user' (hiện tại: {RECORDING_SERVICE.state.value})",
        )
    try:
        RECORDING_SERVICE.advance(session_id=req.session_id, take_sequence=req.take_sequence)
        return {"status": "ok", "message": "Đã bắt đầu lượt thu tiếp theo."}
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/stop")
def stop_recording():
    try:
        RECORDING_SERVICE.stop()
        return {"status": "ok", "message": "Đã gửi yêu cầu dừng phiên thu."}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
