"""Audio recording session control endpoints for child and adult voice study."""
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...child_study import NEGATIVE_PRESETS
from ...recording import RecordingService, SessionConfig

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
    distance_m: float = Field(default=1.0, description="Khoảng cách tới mic (mét)")
    condition: str = Field(default="quiet_normal_voice", description="Điều kiện âm thanh")
    takes_planned: int = Field(default=5, description="Số lượt thu")
    manual_advance: bool = Field(default=True, description="Chờ người dùng bấm trước từng lượt")
    session_id: Optional[str] = Field(default=None, description="Mã phiên tùy chọn")
    mock: bool = Field(default=False, description="Chạy giả lập không mở microphone")


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
def advance_take():
    try:
        RECORDING_SERVICE.advance()
        return {"status": "ok", "message": "Đã bắt đầu lượt thu tiếp theo."}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/stop")
def stop_recording():
    try:
        RECORDING_SERVICE.stop()
        return {"status": "ok", "message": "Đã gửi yêu cầu dừng phiên thu."}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
