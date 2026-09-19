"""Wake Word Lab registry, candidate enrollment, and offline evaluation endpoints."""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from ...wake_lab import (
    EvaluationError,
    WakeCandidate,
    WakeEngine,
    WakeEvaluator,
    WakeRegistry,
    create_candidate_from_samples,
)

router = APIRouter(prefix="/api/wake", tags=["Wake Word Lab"])


class CreateCandidateRequest(BaseModel):
    name: str = Field(..., description="Tên ứng viên model, e.g. STT Nhạy - Thử nghiệm Bé")
    sample_ids: List[str] = Field(..., description="Danh sách sample ID tham chiếu (phải accepted, không thuộc split test)")
    profile: str = Field(default="sensitive", description="standard, sensitive, custom")
    threshold: float = Field(default=0.45, description="Ngưỡng kích hoạt score")
    aliases: List[str] = Field(default_factory=list, description="Danh sách câu gọi alias bổ sung")
    notes: str = Field(default="", description="Ghi chú cấu hình")


class RunEvaluationRequest(BaseModel):
    candidate_ids: List[str] = Field(..., description="Danh sách ID các candidate cần đưa vào so sánh")
    split: str = Field(default="dev", description="dev, pilot, test")
    mode: str = Field(default="official", description="official (chỉ mẫu đã accepted/confirmed) hoặc all")
    name: str = Field(default="", description="Tên bài đánh giá")


@router.get("/candidates")
def list_candidates():
    registry = WakeRegistry()
    candidates = registry.list_candidates()
    return [c.to_dict() for c in candidates]


@router.post("/candidates")
def create_candidate(req: CreateCandidateRequest):
    registry = WakeRegistry()
    try:
        cand = create_candidate_from_samples(
            name=req.name,
            sample_ids=req.sample_ids,
            profile=req.profile,
            threshold=req.threshold,
            alias_config={"aliases": req.aliases} if req.aliases else {},
            notes=req.notes,
            registry=registry,
        )
        return {
            "status": "ok",
            "message": f"Đã tạo thành công candidate '{cand.name}' với {len(cand.reference_sample_ids)} mẫu tham chiếu.",
            "candidate": cand.to_dict(),
        }
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/evaluations")
def list_evaluations():
    registry = WakeRegistry()
    return registry.list_evaluations()


@router.post("/evaluations")
def run_evaluation(req: RunEvaluationRequest):
    registry = WakeRegistry()
    evaluator = WakeEvaluator(registry)
    try:
        res = evaluator.run_evaluation(
            candidate_ids=req.candidate_ids,
            split=req.split,
            mode=req.mode,
            name=req.name,
        )
        return res
    except EvaluationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Lỗi khi chạy đánh giá: {exc}")


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str):
    registry = WakeRegistry()
    ev = registry.get_evaluation(evaluation_id)
    if not ev:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy bài đánh giá '{evaluation_id}'")
    return ev


@router.get("/evaluations/{evaluation_id}/report.md")
def get_evaluation_markdown(evaluation_id: str):
    registry = WakeRegistry()
    ev = registry.get_evaluation(evaluation_id)
    if not ev:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy bài đánh giá '{evaluation_id}'")
    return Response(
        content=ev["report_markdown"],
        media_type="text/markdown",
        headers={"Content-Disposition": f"inline; filename={evaluation_id}.md"},
    )
