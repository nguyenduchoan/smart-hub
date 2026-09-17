"""R2 child-study benchmark semantics layered on top of child_study.

This module intentionally separates dataset selection from file/runtime integrity:
accepted/confirmed samples remain in the benchmark denominator even when their
WAV is missing, corrupt, or has a checksum mismatch. Those failures are reported
as evaluation ERRORs by the existing evaluator instead of being filtered away.
"""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path

from . import child_study as base
from .config import ROOT
from .stt_assets import ARCHIVE_SHA256, BUNDLE, FILES


def _speaker_label(item):
    label = item.get("speaker_label")
    if label:
        return label
    speaker_id = str(item.get("speaker_id", "")).lower()
    if speaker_id.startswith("child"):
        return "child"
    if speaker_id.startswith("adult"):
        return "adult"
    return "unknown"


def official_selection_reason(item):
    """Return None when metadata selects this sample into the official benchmark.

    File existence, checksum matching, WAV readability/format, clipping and model
    processing are deliberately NOT checked here; those belong to evaluation and
    must remain visible in the denominator as ERROR/CLIPPED.
    """
    if item.get("review_status") != "accepted":
        return f"review_status is '{item.get('review_status')}', not 'accepted'"
    if item.get("speaker_confirmed") is not True:
        return "speaker_confirmed is not True"
    if not str(item.get("sample_id", "")).strip():
        return "sample_id is missing"
    if not str(item.get("source", "")).strip():
        return "source is missing"
    if not str(item.get("speaker_id", "")).strip():
        return "speaker_id is missing"
    if not str(item.get("session_id", "")).strip():
        return "session_id is missing"
    label = item.get("label")
    if label not in base.VALID_LABELS:
        return f"invalid label '{label}'"
    expected_events = item.get("expected_events")
    if label == "positive" and expected_events != 1:
        return f"positive label requires expected_events=1, got {expected_events}"
    if label == "negative" and expected_events != 0:
        return f"negative label requires expected_events=0, got {expected_events}"
    if item.get("split") not in base.VALID_SPLITS:
        return f"invalid split '{item.get('split')}'"
    return None


def select_samples(
    all_samples,
    split="dev",
    session_id=None,
    speaker=None,
    label=None,
    allow_unreviewed=False,
):
    """Select benchmark members without performing file/integrity checks."""
    selected = []
    excluded = []
    for item in all_samples:
        if split != "all" and item.get("split") != split:
            continue
        if session_id and item.get("session_id") != session_id:
            continue
        if speaker and _speaker_label(item) != speaker:
            continue
        if label and item.get("label") != label:
            continue

        if allow_unreviewed:
            selected.append(item)
            continue

        reason = official_selection_reason(item)
        if reason is None:
            selected.append(item)
        else:
            excluded.append((item, reason))
    return selected, excluded


def _normalize_integrity_contract(samples):
    """Ensure missing expected SHA becomes a deterministic integrity ERROR.

    The R1 evaluator already turns checksum mismatches and missing files into
    ERROR before the backend is called. Supplying a sentinel checksum for legacy
    accepted records with no source_sha256 reuses that fail-closed path.
    """
    normalized = []
    missing_sha_ids = set()
    for sample in samples:
        copied = deepcopy(sample)
        if not str(copied.get("source_sha256", "")).strip():
            copied["source_sha256"] = "__MISSING_SOURCE_SHA256__"
            missing_sha_ids.add(copied.get("sample_id"))
        normalized.append(copied)
    return normalized, missing_sha_ids


def reproducibility_metadata(profiles, wake_word, aliases, cooldown_seconds, split, evaluation_mode):
    meta = base.get_reproducibility_metadata(
        profiles=profiles,
        wake_word=wake_word,
        aliases=aliases,
        cooldown_seconds=cooldown_seconds,
        split=split,
        evaluation_mode=evaluation_mode,
    )
    meta["stt_model_bundle"] = BUNDLE
    meta["stt_archive_sha256"] = ARCHIVE_SHA256
    meta["model_file_hashes"] = {name: digest for name, (_size, digest) in FILES.items()}
    return meta


def evaluate_dataset(
    samples,
    profiles=("standard", "sensitive"),
    aliases=(),
    wake_word="Maika ơi",
    cooldown_seconds=2.0,
    split="dev",
    evaluation_mode="official",
    root=ROOT,
):
    normalized, missing_sha_ids = _normalize_integrity_contract(samples)
    results = base.evaluate_dataset(
        normalized,
        profiles=profiles,
        aliases=aliases,
        wake_word=wake_word,
        cooldown_seconds=cooldown_seconds,
        split=split,
        evaluation_mode=evaluation_mode,
        root=root,
    )

    # Restore source metadata for reporting and make the integrity error explicit.
    original_by_id = {s.get("sample_id"): s for s in samples}
    for item in results.get("samples", []):
        sample_id = item.get("sample_id")
        original = original_by_id.get(sample_id, {})
        item["source_sha256"] = original.get("source_sha256")
        if sample_id in missing_sha_ids:
            for evaluation in item.get("evaluations", {}).values():
                evaluation["status"] = "ERROR"
                evaluation["is_success"] = False
                evaluation["error"] = "source_sha256 is missing; integrity provenance cannot be verified"
                evaluation["expected_sha256"] = None

    # Metrics must reflect post-processed missing-SHA ERRORs. Recalculate only if
    # a legacy accepted sample had no SHA; ordinary missing/mismatch files were
    # already ERROR in R1 and are denominator-correct now that selection keeps them.
    if missing_sha_ids:
        results = _recount_metrics(results, profiles)

    results["reproducibility"] = reproducibility_metadata(
        profiles, wake_word, aliases, cooldown_seconds, split, evaluation_mode
    )
    return results


def _recount_metrics(results, profiles):
    """Recompute numerator/denominator counters after R2 integrity normalization."""
    samples = results.get("samples", [])
    for prof in profiles:
        m = results["metrics"].get(prof, {})
        pos = {
            "eligible": 0, "processed": 0, "accurate": 0, "missed": 0,
            "duplicate": 0, "clipped": 0, "errors": 0,
        }
        neg = {
            "eligible": 0, "processed": 0, "correct_reject": 0,
            "false_alarm": 0, "clipped": 0, "errors": 0,
        }
        for item in samples:
            ev = item.get("evaluations", {}).get(prof, {})
            status = ev.get("status")
            target = pos if item.get("label") == "positive" else neg
            target["eligible"] += 1
            if status == "ERROR":
                target["errors"] += 1
                continue
            target["processed"] += 1
            if status == "ACCURATE":
                pos["accurate"] += 1
            elif status == "MISSED":
                pos["missed"] += 1
            elif status == "DUPLICATE":
                pos["duplicate"] += 1
            elif status == "CORRECT_REJECT":
                neg["correct_reject"] += 1
            elif status == "FALSE_ALARM":
                neg["false_alarm"] += 1
            elif status == "CLIPPED":
                target["clipped"] += 1

        pos["accurate_rate"] = pos["accurate"] / pos["eligible"] if pos["eligible"] else 0.0
        pos["frr"] = pos["missed"] / pos["eligible"] if pos["eligible"] else 0.0
        pos["duplicate_rate"] = pos["duplicate"] / pos["eligible"] if pos["eligible"] else 0.0
        neg["far"] = neg["false_alarm"] / neg["eligible"] if neg["eligible"] else 0.0
        m["eligible_total"] = len(samples)
        m["processed_total"] = pos["processed"] + neg["processed"]
        m["errors"] = pos["errors"] + neg["errors"]
        m["positive"] = pos
        m["negative"] = neg
    results["eligible_total"] = len(samples)
    results["total_samples"] = len(samples)
    return results


def format_evaluation_markdown(results):
    """Reuse the detailed R1 report but remove misleading aggregate targets."""
    report = base.format_evaluation_markdown(results)
    report = report.replace("Mục tiêu pilot", "Ghi chú")
    report = report.replace("100% yên tĩnh, ≥90% nhiễu/xa", "Xem acceptance theo từng nhóm")
    report = report.replace("0% yên tĩnh, ≤10% nhiễu/xa", "Xem acceptance theo từng nhóm")
    report = report.replace("0 lượt trùng", "Xem acceptance theo từng nhóm")
    report = report.replace("| 0% |", "| Xem acceptance theo từng nhóm |")
    report = report.replace("| < 0.100 |", "| Chỉ số chẩn đoán; không phải acceptance gộp |")
    report = report.replace("| < 0.500s |", "| Chỉ số chẩn đoán; không phải acceptance gộp |")
    report = report.replace("| 0 |\n", "| 0 ERROR |\n")
    note = (
        "\n> Acceptance được đánh giá theo từng speaker/condition/split ở bảng nhóm; "
        "không suy PASS từ tỷ lệ aggregate.\n"
    )
    marker = "## 2. Chi tiết từng nhóm thử nghiệm"
    return report.replace(marker, note + "\n" + marker)


def save_evaluation_results(results, output_path=None, force=False):
    """R2 saver using the R2 markdown formatter while preserving overwrite safety."""
    if output_path is None:
        base.ensure_child_study_dirs()
        ts = datetime.now().strftime("%Y%m%d-%H%M%S_%f")
        output_path = base.RESULTS_DIR / f"eval-{ts}.json"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    md_path = output_path.with_suffix(".md")
    if not force and (output_path.exists() or md_path.exists()):
        existing = output_path if output_path.exists() else md_path
        raise FileExistsError(f"File '{existing}' đã tồn tại; dùng --force nếu muốn ghi đè.")

    old_umask = os.umask(0o077)
    try:
        tmp_json = output_path.with_suffix(".tmp.json")
        tmp_md = output_path.with_suffix(".tmp.md")
        tmp_json.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_md.write_text(format_evaluation_markdown(results), encoding="utf-8")
        tmp_json.replace(output_path)
        tmp_md.replace(md_path)
    finally:
        os.umask(old_umask)
    return output_path
