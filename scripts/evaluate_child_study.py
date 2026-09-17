#!/usr/bin/env python3
"""Runner offline so sánh hai profile (standard / sensitive) trên cùng tập WAV (CV-05).
Đọc nhãn đã duyệt từ child-study/labels.jsonl hoặc manifest thư mục.
Không mở audio devices, không gửi dữ liệu, không đổi cấu hình runtime.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smart_hub.config import load_config
from smart_hub.child_study import (
    LABELS_FILE,
    load_labels,
    evaluate_dataset,
    format_evaluation_markdown,
    save_evaluation_results,
    compute_file_sha256,
)


def main():
    parser = argparse.ArgumentParser(
        description="Đánh giá offline wake word giọng trẻ em / người lớn trên các profile (CV-05)."
    )
    parser.add_argument("--split", choices=("pilot", "dev", "test", "all"), default="all",
                        help="Chọn tập dữ liệu cần đánh giá (mặc định all).")
    parser.add_argument("--profile", choices=("standard", "sensitive", "both"), default="both",
                        help="Profile VAD/STT cần chạy (mặc định both).")
    parser.add_argument("--session-id", default=None,
                        help="Lọc theo mã phiên (session_id).")
    parser.add_argument("--speaker", choices=("child", "adult"), default=None,
                        help="Lọc theo người nói (child hoặc adult).")
    parser.add_argument("--dir", type=Path, default=None,
                        help="Đánh giá trực tiếp một thư mục thu âm (dùng manifest.json của thư mục).")
    parser.add_argument("--wav", type=Path, default=None,
                        help="Đánh giá nhanh một file WAV đơn lẻ.")
    parser.add_argument("--label", choices=("positive", "negative"), default=None,
                        help="Lọc theo nhãn dương tính hoặc âm tính.")
    parser.add_argument("--alias", action="append", default=[],
                        help="Cách viết khác cho câu gọi (ví dụ 'mai ca ơi').")
    parser.add_argument("--output", type=Path, default=None,
                        help="Đường dẫn file JSON lưu kết quả (mặc định lưu vào child-study/results/).")

    args = parser.parse_args()
    config = load_config()

    profiles = ("standard", "sensitive") if args.profile == "both" else (args.profile,)

    samples = []
    if args.wav:
        wav_path = args.wav if args.wav.is_absolute() else (ROOT / args.wav)
        if not wav_path.exists():
            print(f"[ERROR] Không tìm thấy file: {wav_path}", file=sys.stderr)
            sys.exit(1)
        sha256 = compute_file_sha256(wav_path)
        label = args.label or "positive"
        samples.append({
            "sample_id": wav_path.stem,
            "source": str(wav_path.relative_to(ROOT) if wav_path.is_relative_to(ROOT) else wav_path),
            "source_sha256": sha256,
            "speaker_id": args.speaker or "unknown",
            "session_id": "single_wav",
            "split": args.split if args.split != "all" else "manual",
            "label": label,
            "transcript_human": config.wake_word if label == "positive" else "(negative phrase)",
            "expected_events": 1 if label == "positive" else 0,
            "condition": "manual_wav",
            "distance_m": 1.0,
            "speaker_confirmed": False,
            "review_status": "manual",
        })
    elif args.dir:
        dir_path = args.dir if args.dir.is_absolute() else (ROOT / args.dir)
        manifest_file = dir_path / "manifest.json"
        if not manifest_file.exists():
            print(f"[ERROR] Không tìm thấy manifest.json trong: {dir_path}", file=sys.stderr)
            sys.exit(1)
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        session_id = manifest.get("session_id", dir_path.name)
        speaker_id = manifest.get("speaker_id", manifest.get("speaker_label", "unknown"))
        split = manifest.get("split", "pilot")
        label = manifest.get("label", "positive")
        expected_events = manifest.get("expected_events", 1 if label == "positive" else 0)
        distance_m = manifest.get("distance_m", 1.0)
        condition = manifest.get("condition", "quiet_normal_voice")
        expected_phrase = manifest.get("expected_phrase", config.wake_word)

        for idx, clip in enumerate(manifest.get("clips", []), start=1):
            clip_file = dir_path / clip["file"]
            rel_path = str(clip_file.relative_to(ROOT) if clip_file.is_relative_to(ROOT) else clip_file)
            samples.append({
                "sample_id": f"{session_id}-t{idx:02d}",
                "source": rel_path,
                "source_sha256": clip.get("sha256", ""),
                "speaker_id": speaker_id,
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
    else:
        # Load from child-study/labels.jsonl
        all_labels = load_labels(LABELS_FILE)
        for item in all_labels:
            if args.split != "all" and item.get("split") != args.split:
                continue
            if args.session_id and item.get("session_id") != args.session_id:
                continue
            if args.speaker and not str(item.get("speaker_id", "")).startswith(args.speaker):
                continue
            if args.label and item.get("label") != args.label:
                continue
            samples.append(item)

    if not samples:
        print(f"[INFO] Không có mẫu nào thỏa mãn điều kiện lọc (split={args.split}, session={args.session_id}).", file=sys.stderr)
        print(f"Để đánh giá một thư mục: --dir <recordings/...> hoặc kiểm tra {LABELS_FILE}", file=sys.stderr)
        sys.exit(0)

    print(f"[RUNNER] Đang đánh giá {len(samples)} mẫu WAV trên profile: {', '.join(profiles)}...", flush=True)
    results = evaluate_dataset(samples, profiles=profiles, aliases=args.alias, wake_word=config.wake_word)

    out_file = save_evaluation_results(results, args.output)
    md_report = format_evaluation_markdown(results)

    print(f"\n[SAVED] Đã lưu kết quả JSON: {out_file}", flush=True)
    print(f"[SAVED] Đã lưu báo cáo Markdown: {out_file.with_suffix('.md')}\n", flush=True)
    print(md_report, flush=True)


if __name__ == "__main__":
    main()
