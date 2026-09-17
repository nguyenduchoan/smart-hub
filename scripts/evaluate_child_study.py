#!/usr/bin/env python3
"""Offline child/adult wake-word evaluation runner (CV-05, R2 semantics).

Official mode selects reviewed/confirmed samples by metadata, then evaluates
file integrity inside the benchmark so missing/corrupt/SHA-mismatch samples stay
in the denominator as ERROR instead of disappearing before scoring.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smart_hub.config import load_config
from smart_hub.child_study import LABELS_FILE, load_labels, compute_file_sha256
from smart_hub.child_study_r2 import (
    select_samples,
    evaluate_dataset,
    format_evaluation_markdown,
    save_evaluation_results,
)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Đánh giá offline wake word giọng trẻ em / người lớn trên các profile (CV-05)."
    )
    parser.add_argument(
        "--split", choices=("pilot", "dev", "test", "all"), default="dev",
        help="Chọn tập dữ liệu cần đánh giá (mặc định dev; test chỉ chạy sau khi chốt candidate).",
    )
    parser.add_argument(
        "--profile", choices=("standard", "sensitive", "both"), default="both",
        help="Profile VAD/STT cần chạy (mặc định both).",
    )
    parser.add_argument("--session-id", default=None, help="Lọc theo mã phiên (session_id).")
    parser.add_argument(
        "--speaker", choices=("child", "adult"), default=None,
        help="Lọc theo người nói (child hoặc adult).",
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--dir", type=Path, default=None,
        help="Đánh giá ad-hoc một thư mục thu âm (cần --allow-unreviewed).",
    )
    source_group.add_argument(
        "--wav", type=Path, default=None,
        help="Đánh giá ad-hoc một file WAV đơn lẻ (cần --allow-unreviewed).",
    )
    parser.add_argument(
        "--allow-unreviewed", action="store_true",
        help="Cho phép đánh giá dữ liệu chưa duyệt hoặc ad-hoc từ --wav/--dir.",
    )
    parser.add_argument(
        "--label", choices=("positive", "negative"), default=None,
        help="Lọc theo nhãn dương tính hoặc âm tính.",
    )
    parser.add_argument(
        "--alias", action="append", default=[],
        help="Cách viết khác cho câu gọi (ví dụ 'mai ca ơi').",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Đường dẫn file JSON lưu kết quả (mặc định child-study/results/).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Cho phép ghi đè file kết quả nếu đã tồn tại.",
    )
    return parser


def _samples_from_wav(args, config):
    wav_path = args.wav if args.wav.is_absolute() else (ROOT / args.wav)
    if not wav_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {wav_path}")
    label = args.label or "positive"
    speaker_label = args.speaker or "child"
    return [{
        "sample_id": wav_path.stem,
        "source": str(wav_path.relative_to(ROOT) if wav_path.is_relative_to(ROOT) else wav_path),
        "source_sha256": compute_file_sha256(wav_path),
        "speaker_id": f"{speaker_label}_01",
        "speaker_label": speaker_label,
        "session_id": "single_wav",
        "split": args.split if args.split != "all" else "manual",
        "label": label,
        "transcript_human": config.wake_word if label == "positive" else "(negative phrase)",
        "expected_events": 1 if label == "positive" else 0,
        "condition": "manual_wav",
        "distance_m": 1.0,
        "speaker_confirmed": False,
        "review_status": "manual",
    }]


def _samples_from_dir(args, config):
    dir_path = args.dir if args.dir.is_absolute() else (ROOT / args.dir)
    manifest_file = dir_path / "manifest.json"
    if not manifest_file.exists():
        raise FileNotFoundError(f"Không tìm thấy manifest.json trong: {dir_path}")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Malformed manifest.json: {exc}") from exc

    session_id = manifest.get("session_id", dir_path.name)
    speaker_id = manifest.get("speaker_id", "unknown")
    speaker_label = manifest.get("speaker_label", args.speaker or "child")
    split = manifest.get("split", "pilot")
    label = manifest.get("label", "positive")
    expected_events = manifest.get("expected_events", 1 if label == "positive" else 0)
    distance_m = manifest.get("distance_m", 1.0)
    condition = manifest.get("condition", "quiet_normal_voice")
    expected_phrase = manifest.get("expected_phrase", config.wake_word)

    samples = []
    for idx, clip in enumerate(manifest.get("clips", []), start=1):
        clip_file = dir_path / clip["file"]
        rel_path = str(clip_file.relative_to(ROOT) if clip_file.is_relative_to(ROOT) else clip_file)
        samples.append({
            "sample_id": f"{session_id}-t{idx:02d}",
            "source": rel_path,
            "source_sha256": clip.get("sha256", ""),
            "speaker_id": speaker_id,
            "speaker_label": speaker_label,
            "session_id": session_id,
            "split": split,
            "label": label,
            "transcript_human": expected_phrase,
            "expected_events": expected_events,
            "condition": condition,
            "distance_m": distance_m,
            "speaker_confirmed": manifest.get("speaker_confirmed", False),
            "review_status": manifest.get("status", "captured_pending_review"),
        })
    return samples


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config()
    profiles = ("standard", "sensitive") if args.profile == "both" else (args.profile,)

    if args.wav or args.dir:
        if not args.allow_unreviewed:
            parser.error("--wav và --dir chỉ dùng cho ad-hoc; cần --allow-unreviewed")
        evaluation_mode = "ad_hoc"
    else:
        evaluation_mode = "unreviewed" if args.allow_unreviewed else "official"

    try:
        if args.wav:
            raw_samples = _samples_from_wav(args, config)
        elif args.dir:
            raw_samples = _samples_from_dir(args, config)
        else:
            raw_samples = load_labels(LABELS_FILE)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    selected, excluded = select_samples(
        raw_samples,
        split=args.split,
        session_id=args.session_id,
        speaker=args.speaker,
        label=args.label,
        allow_unreviewed=args.allow_unreviewed,
    )

    if not selected:
        print(
            f"[ERROR] Không có mẫu nào thuộc benchmark sau selection "
            f"(split={args.split}, session={args.session_id}, speaker={args.speaker}, label={args.label}, mode={evaluation_mode}).",
            file=sys.stderr,
        )
        for item, reason in excluded[:5]:
            print(f"  - {item.get('sample_id')}: {reason}", file=sys.stderr)
        return 2

    print(
        f"[RUNNER] Đang đánh giá {len(selected)} mẫu trên profile: {', '.join(profiles)} "
        f"(mode={evaluation_mode}, split={args.split})...",
        flush=True,
    )
    results = evaluate_dataset(
        selected,
        profiles=profiles,
        aliases=args.alias,
        wake_word=config.wake_word,
        split=args.split,
        evaluation_mode=evaluation_mode,
        root=ROOT,
    )

    try:
        out_file = save_evaluation_results(results, args.output, force=args.force)
    except FileExistsError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    report = format_evaluation_markdown(results)
    print(f"\n[SAVED] JSON: {out_file}", flush=True)
    print(f"[SAVED] Markdown: {out_file.with_suffix('.md')}\n", flush=True)
    print(report, flush=True)

    has_errors = any(m.get("errors", 0) > 0 for m in results["metrics"].values())
    if has_errors:
        print("[WARN] Benchmark có processing/integrity ERROR; xem report.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
