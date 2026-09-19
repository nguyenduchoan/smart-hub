"""Wake word registry, enrollment, and offline evaluation package."""
from .enrollment import create_candidate_from_samples
from .evaluator import EvaluationError, WakeEvaluator
from .registry import (
    ModelArtifact,
    WakeCandidate,
    WakeEngine,
    WakeRegistry,
)

__all__ = [
    "EvaluationError",
    "ModelArtifact",
    "WakeCandidate",
    "WakeEngine",
    "WakeEvaluator",
    "WakeRegistry",
    "create_candidate_from_samples",
]
