"""R2 policy helpers for child-study benchmark selection and reporting.

Keep dataset membership separate from runtime/data-integrity checks: accepted and
confirmed samples stay in the official denominator even if their WAV later goes
missing or fails checksum validation. The evaluator then records those failures as
ERROR instead of silently shrinking the benchmark.
"""
from pathlib import Path

from .child_study import VALID_LABELS, VALID_SPLITS, filter_samples

MISSING_SHA_SENTINEL = "__MISSING_SOURCE_SHA256__"
MISSING_SOURCE_PREFIX = "__MISSING_SOURCE__/"


def _metadata_reason(item):
    if item.get("review_status") != "accepted":
        return f"review_status is '{item.get('review_status')}', not 'accepted'"
    if item.get("speaker_confirmed") is not True:
        return "speaker_confirmed is not True"

    label = item.get("label")
    if label not in VALID_LABELS:
        return f"invalid label '{label}'"

    expected = item.get("expected_events")
    if label == "positive" and expected != 1:
        return f"positive label requires expected_events=1, got {expected}"
    if label == "negative" and expected != 0:
        return f"negative label requires expected_events=0, got {expected}"

    split = item.get("split")
    if split not in VALID_SPLITS:
        return f"invalid split '{split}'"

    sample_id = item.get("sample_id")
    if not sample_id or not str(sample_id).strip():
        return "sample_id is missing"
    session_id = item.get("session_id")
    if not session_id or not str(session_id).strip():
        return "session_id is missing"
    speaker_id = item.get("speaker_id")
    if not speaker_id or not str(speaker_id).strip():
        return "speaker_id is missing"

    return None


def select_samples_for_evaluation(
    all_samples,
    *,
    split="dev",
    session_id=None,
    speaker=None,
    label=None,
    allow_unreviewed=False,
    root=None,
):
    """Apply user selectors, then official metadata policy without WAV integrity filtering.

    WAV existence/checksum/format are evaluation concerns. Official samples that
    have already been accepted and speaker-confirmed remain selected even when
    those integrity checks fail later, so ERROR stays in the denominator.
    """
    kwargs = {
        "split": split,
        "session_id": session_id,
        "speaker": speaker,
        "label": label,
        "allow_unreviewed": True,
    }
    if root is not None:
        kwargs["root"] = root
    selected, _ = filter_samples(all_samples, **kwargs)

    if allow_unreviewed:
        return [dict(item) for item in selected], []

    eligible = []
    ineligible = []
    for item in selected:
        reason = _metadata_reason(item)
        if reason:
            ineligible.append((item, reason))
            continue

        normalized = dict(item)
        if not normalized.get("source"):
            safe_id = str(normalized.get("sample_id") or "unknown").replace("/", "_")
            normalized["source"] = f"{MISSING_SOURCE_PREFIX}{safe_id}.wav"
        if not normalized.get("source_sha256"):
            normalized["source_sha256"] = MISSING_SHA_SENTINEL
        eligible.append(normalized)

    return eligible, ineligible


def finalize_evaluation_results(results):
    """Repair diagnostics for policy sentinels and inject authoritative provenance."""
    for sample in results.get("samples", []):
        source_missing = str(sample.get("source", "")).startswith(MISSING_SOURCE_PREFIX)
        sha_missing = sample.get("source_sha256") == MISSING_SHA_SENTINEL

        if source_missing:
            sample["source"] = None
        if sha_missing:
            sample["source_sha256"] = None

        if source_missing or sha_missing:
            for evaluation in sample.get("evaluations", {}).values():
                if evaluation.get("status") != "ERROR":
                    continue
                if source_missing:
                    evaluation["error"] = "source is missing"
                    evaluation["file"] = None
                elif sha_missing:
                    evaluation["error"] = "source_sha256 is missing"
                    evaluation["expected_sha256"] = None

    repro = results.setdefault("reproducibility", {})
    try:
        from . import stt_assets

        repro["stt_model_bundle"] = stt_assets.BUNDLE
        repro["stt_archive_sha256"] = stt_assets.ARCHIVE_SHA256
        repro["model_file_hashes"] = {
            name: digest for name, (_size, digest) in stt_assets.FILES.items()
        }
        repro.pop("model_provenance_error", None)
    except Exception as exc:
        repro["stt_model_bundle"] = "unknown"
        repro["stt_archive_sha256"] = "unknown"
        repro["model_file_hashes"] = {}
        repro["model_provenance_error"] = str(exc)

    return results


def format_report_without_aggregate_targets(markdown):
    """Remove condition-specific acceptance targets from the aggregate profile table."""
    lines = markdown.splitlines()
    inside_summary = False
    output = []
    inserted_note = False

    for line in lines:
        if line.startswith("## 1. "):
            inside_summary = True
            output.append(line)
            continue
        if line.startswith("## 2. "):
            inside_summary = False

        if inside_summary and line.startswith("| "):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 3:
                cells = cells[:-1]
                line = "| " + " | ".join(cells) + " |"

        output.append(line)
        if inside_summary and not inserted_note and line == "":
            output.append(
                "> Acceptance targets are condition/group-specific; compare the group rows with "
                "`docs/CHILD_VOICE_RECORDING_PLAN.md` instead of treating the aggregate rate as PASS/FAIL."
            )
            output.append("")
            inserted_note = True

    return "\n".join(output)
