import time
from fastapi import APIRouter
from pydantic import BaseModel

from ...child_study import load_labels
from ...devices import DeviceStorage
from ...locks import ResourceLock
from ..security import CSRF_TOKEN, SESSION_TOKEN, issue_csrf_token

router = APIRouter(prefix="/api", tags=["System"])


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str = "smart-hub-dashboard"
    version: str = "1.0.0"


@router.get("/health", response_model=HealthResponse)
def get_health():
    return HealthResponse()


@router.get("/csrf-token")
def get_csrf_token():
    ttl = 86400
    token = issue_csrf_token(ttl_seconds=ttl)
    return {"csrf_token": token, "expires_at": time.time() + ttl}


@router.get("/status")
def get_system_status():
    storage = DeviceStorage()
    gateways = storage.list_gateways()
    appliances = storage.list_appliances()

    # Audio resource lock check
    mic_lock = ResourceLock("audio_capture")
    is_mic_busy = mic_lock.is_locked()

    # Child-study samples waiting for review
    labels = load_labels()
    pending_reviews = sum(
        1 for l in labels if l.get("review_status") in ("captured_pending_review", "needs_review")
    )
    accepted_samples = sum(
        1 for l in labels if l.get("review_status") == "accepted" and l.get("speaker_confirmed")
    )

    return {
        "gateways_count": len(gateways),
        "online_gateways": sum(1 for g in gateways if g.status.value == "online"),
        "appliances_count": len(appliances),
        "mic_busy": is_mic_busy,
        "samples_total": len(labels),
        "pending_reviews": pending_reviews,
        "accepted_samples": accepted_samples,
    }
