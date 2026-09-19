import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import wave
from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from datetime import datetime
from ...audio import pcm_stats
from ...child_study import (
    CHILD_STUDY_DIR,
    LABELS_FILE,
    compute_file_sha256,
    load_labels,
    load_sessions,
    save_label,
)
from ...config import ROOT, load_config
from ...locks import dataset_lock

router = APIRouter(prefix="/api/samples", tags=["Samples & Review"])


class ReviewSampleRequest(BaseModel):
    review_status: str = Field(..., description="accepted, rejected, needs_review")
    speaker_confirmed: bool = Field(default=False, description="Xác nhận người nói thực tế")
    transcript_confirmed: Optional[str] = Field(default=None, description="Nội dung nghe được thực tế")
    review_note: str = Field(default="", description="Lý do hoặc ghi chú")
    reviewer: str = Field(default="dashboard_reviewer", description="Tên người duyệt")
    expected_status: Optional[str] = Field(default=None, description="Trạng thái kỳ vọng trước khi cập nhật")
    expected_revision: Optional[int] = Field(default=None, description="Revision kỳ vọng trước khi cập nhật")
    expected_etag: Optional[str] = Field(default=None, description="ETag kỳ vọng trước khi cập nhật")
    label: Optional[str] = Field(default=None, description="Nhãn ground truth explicit ('positive' hoặc 'negative')")
    expected_events: Optional[int] = Field(default=None, description="Số sự kiện wake word explicit (0 hoặc 1)")


def _resolve_audio_path(source_rel: str) -> Path:
    """Safely resolve source audio path inside recordings/ preventing path traversal."""
    cleaned = os.path.normpath(source_rel)
    if cleaned.startswith("..") or cleaned.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid relative audio path.")
    full_path = (ROOT / cleaned).resolve()
    recordings_dir = (ROOT / "recordings").resolve()
    if not str(full_path).startswith(str(recordings_dir)):
        raise HTTPException(status_code=403, detail="Audio file must reside inside recordings directory.")
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail=f"Audio file not found: {cleaned}")
    return full_path


@router.get("")
def list_samples(
    speaker: Optional[str] = None,
    speaker_id: Optional[str] = None,
    split: Optional[str] = None,
    review_status: Optional[str] = None,
    label: Optional[str] = None,
):
    all_labels = load_labels()
    filtered = []
    for s in all_labels:
        if speaker and s.get("speaker_label") != speaker:
            continue
        if speaker_id and s.get("speaker_id") != speaker_id:
            continue
        if split and s.get("split") != split:
            continue
        if review_status and s.get("review_status") != review_status:
            continue
        if label and s.get("label") != label:
            continue
        filtered.append(s)

    return {
        "total": len(all_labels),
        "count": len(filtered),
        "samples": filtered,
    }


@router.get("/{sample_id}")
def get_sample(sample_id: str):
    all_labels = load_labels()
    for s in all_labels:
        if s.get("sample_id") == sample_id:
            return s
    raise HTTPException(status_code=404, detail=f"Không tìm thấy mẫu '{sample_id}'")


@router.get("/{sample_id}/audio")
def stream_sample_audio(sample_id: str):
    all_labels = load_labels()
    target_sample = None
    for s in all_labels:
        if s.get("sample_id") == sample_id:
            target_sample = s
            break
    if not target_sample:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy mẫu '{sample_id}'")

    source_path = _resolve_audio_path(target_sample.get("source", ""))
    return FileResponse(
        path=str(source_path),
        media_type="audio/wav",
        filename=source_path.name,
    )


@router.patch("/{sample_id}/review")
def review_sample(
    sample_id: str,
    req: ReviewSampleRequest,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
):
    valid_statuses = ("accepted", "rejected", "needs_review")
    if req.review_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Trạng thái review không hợp lệ. Chọn một trong: {valid_statuses}",
        )

    # Mandatory rule: Acceptance requires speaker confirmation and transcript confirmation
    if req.review_status == "accepted":
        if not req.speaker_confirmed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Quyết định 'Chấp nhận' bắt buộc phải xác nhận đúng người nói (speaker_confirmed=True).",
            )
        if not req.transcript_confirmed or not req.transcript_confirmed.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Quyết định 'Chấp nhận' bắt buộc người duyệt xác nhận nội dung nghe được thực tế.",
            )

    try:
        with dataset_lock(timeout=5.0):
            all_labels = load_labels()
            target = next((s for s in all_labels if s.get("sample_id") == sample_id), None)
            if not target:
                raise HTTPException(status_code=404, detail=f"Mẫu '{sample_id}' không tồn tại.")

            # Concurrency and revision checks (V2-14)
            if if_match and target.get("etag") and if_match.strip('"') != target.get("etag"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"ETag mismatch trong If-Match header (kỳ vọng '{if_match}', hiện tại '{target.get('etag')}').",
                )
            if req.expected_revision is not None and target.get("revision", 1) != req.expected_revision:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Revision mismatch: kỳ vọng {req.expected_revision}, hiện tại {target.get('revision', 1)}. Vui lòng tải lại.",
                )
            if req.expected_etag is not None and target.get("etag") != req.expected_etag:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"ETag mismatch: kỳ vọng {req.expected_etag}, hiện tại {target.get('etag')}. Vui lòng tải lại.",
                )
            if req.expected_status and target.get("review_status") != req.expected_status:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Trạng thái mẫu đã bị thay đổi bởi thao tác khác (hiện tại: {target.get('review_status')}). Vui lòng tải lại.",
                )

            # R09 / V2-04: Full technical QC verification if status is accepted
            if req.review_status == "accepted":
                audio_path = _resolve_audio_path(target.get("source", ""))
                # Verify SHA256 checksum (missing source_sha256 must be rejected)
                expected_sha = target.get("source_sha256")
                if not expected_sha or not str(expected_sha).strip():
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Mẫu thiếu source_sha256 gốc trong nhãn; không thể duyệt 'accepted' khi chưa có hash gốc xác thực.",
                    )
                actual_sha = compute_file_sha256(audio_path)
                if actual_sha.lower() != str(expected_sha).lower():
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"File audio bị thay đổi checksum SHA256 (kỳ vọng {expected_sha[:8]}, thực tế {actual_sha[:8]}).",
                    )
                # Verify WAV headers and PCM clipping
                try:
                    with wave.open(str(audio_path), "rb") as wf:
                        channels = wf.getnchannels()
                        rate = wf.getframerate()
                        sampwidth = wf.getsampwidth()
                        comptype = wf.getcomptype()
                        if (channels, rate, sampwidth, comptype) != (1, 16000, 2, "NONE"):
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Audio không đạt chuẩn QC (cần 16kHz mono 16-bit PCM; thực tế {rate}Hz, {channels}ch, {sampwidth*8}bit).",
                            )
                        nframes = wf.getnframes()
                        pcm = wf.readframes(nframes)
                        if len(pcm) != nframes * 2 or len(pcm) == 0:
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="File WAV rỗng hoặc bị cắt cụt (không đủ dữ liệu PCM theo nframes).",
                            )
                        stats = pcm_stats(pcm)
                        if stats["clipped_percent"] > 1.0:
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Audio clipping nghiêm trọng ({stats['clipped_percent']:.2f}% > 1.0%); vi phạm chuẩn chất lượng QC.",
                            )
                        if stats["rms"] < 5.0:
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Audio quá nhỏ hoặc im lặng (RMS {stats['rms']} < 5.0); không đạt chuẩn QC.",
                            )
                        target["technical_qc"] = {
                            "version": "v1",
                            "stats": {
                                "seconds": round(nframes / 16000, 2),
                                "samples": nframes,
                                "peak": stats["peak"],
                                "rms": stats["rms"],
                                "clipped_percent": stats["clipped_percent"],
                                "sha256": actual_sha,
                            },
                            "passed": True,
                            "verified_at": datetime.now().astimezone().isoformat(),
                        }
                except wave.Error as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"File WAV hỏng hoặc không đúng định dạng: {exc}",
                    )

            # Perform update
            target["review_status"] = req.review_status
            target["speaker_confirmed"] = req.speaker_confirmed
            if req.transcript_confirmed is not None:
                target["transcript_human"] = req.transcript_confirmed.strip()

            # V2-05: Ground truth label and expected_events are NEVER auto-flipped from transcript text!
            # Only update if explicitly provided by reviewer
            if req.label is not None:
                if req.label not in ("positive", "negative"):
                    raise HTTPException(status_code=400, detail=f"Nhãn không hợp lệ: '{req.label}'")
                target["label"] = req.label
            if req.expected_events is not None:
                if req.expected_events not in (0, 1):
                    raise HTTPException(status_code=400, detail=f"expected_events phải là 0 hoặc 1, nhận: {req.expected_events}")
                target["expected_events"] = req.expected_events

            # Ground truth consistency check
            if target.get("label") == "positive" and target.get("expected_events") != 1:
                raise HTTPException(status_code=400, detail="Mẫu positive yêu cầu expected_events=1.")
            if target.get("label") == "negative" and target.get("expected_events") != 0:
                raise HTTPException(status_code=400, detail="Mẫu negative yêu cầu expected_events=0.")

            target["reviewer"] = req.reviewer
            target["review_note"] = req.review_note
            target["reviewed_at"] = datetime.now().astimezone().isoformat()
            save_label(target, acquire_lock=False)

            return {
                "status": "ok",
                "message": f"Đã cập nhật trạng thái duyệt: {req.review_status}",
                "sample": target,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi cập nhật review: {exc}")
