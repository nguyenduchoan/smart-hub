#!/usr/bin/env python3
"""Runner offline so sánh hai profile (standard / sensitive) trên cùng tập WAV (CV-05).
Mặc định chỉ dùng mẫu đã được duyệt (accepted, confirmed, SHA-256 khớp) trên tập dev.
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
    filter_samples,
    evaluate_dataset,
    format_evaluation_markdown,
    save_evaluation_results,
    compute_file_sha256,
)


def main():
    parser = argparse.ArgumentParser(
        description="Đánh giá offline wake word giọng trẻ em / người lớn trên các profile (CV-05)."
    )
    parser.add_argument(
        "--split",
        choices=("pilot", "dev", "test", "all"),
        default="dev",
        help="Chọn tập dữ liệu cần đánh giá (mặc định dev; test chỉ chạy sau khi chốt candidate).",
    )
    parser.add_argument(
        "--profile",
        choices=("standard", "sensitive", "both"),
        default="both",
        help="Profile VAD/STT cần chạy (mặc định both).",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Lọc theo mã phiên (session_id).",
    )
    parser.add_argument(
        "--speaker",
        choices=("child", "adult"),
        default=None,
        help="Lọc theo người nói (child hoặc adult).",
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Đánh giá ad-hoc một thư mục thu âm (cần --allow-unreviewed).",
    )
    source_group.add_argument(
        "--wav",
        type=Path,
        default=None,
        help="Đánh giá ad-hoc một file WAV đơn lẻ (cần --allow-unreviewed).",
    )
    parser.add_argument(
        "--allow-unreviewed",
        action="store_true",
        help="Cho phép đánh giá dữ liệu chưa duyệt hoặc ad-hoc từ --wav/--dir.",
    )
    parser.add_argument(
        "--label",
        choices=("positive", "negative"),
        default=None,
        help="Lọc theo nhãn dương tính hoặc âm tính.",
    )
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help="Cách viết khác cho câu gọi (ví dụ 'mai ca ơi').",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Đường dẫn file JSON lưu kết quả (mặc định lưu vào child-study/results/).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Cho phép ghi đè file kết quả nếu đã tồn tại.",
    )

    args = parser.parse_args()
    config = load_config()

    profiles = ("standard", "sensitive") if args.profile == "both" else (args.profile,)

    if args.wav or args.dir:
        if not args.allow_unreviewed:
            print(
                "[ERROR] --wav và --dir chỉ dùng cho chế độ kiểm thử nhanh / ad-hoc. "
                "Cần truyền thêm cờ --allow-unreviewed để chạy.",
                file=sys.stderr,
            )
            sys.exit(2)
        evaluation_mode = "ad_hoc"
    else:
        evaluation_mode = "unreviewed" if args.allow_unreviewed else "official"

    raw_samples = []
    if args.wav:
        wav_path = args.wav if args.wav.is_absolute() else (ROOT / args.wav)
        if not wav_path.exists():
            print(f"[ERROR] Không tìm thấy file: {wav_path}", file=sys.stderr)
            sys.exit(1)
        sha256 = compute_file_sha256(wav_path)
        lbl = args.label or "positive"
        spk_label = args.speaker or "child"
        raw_samples.append({
            "sample_id": wav_path.stem,
            "source": str(wav_path.relative_to(ROOT) if wav_path.is_relative_to(ROOT) else wav_path),
            "source_sha256": sha256,
            "speaker_id": f"{spk_label}_01",
            "speaker_label": spk_label,
            "session_id": "single_wav",
            "split": args.split if args.split != "all" else "manual",
            "label": lbl,
            "transcript_human": config.wake_word if lbl == "positive" else "(negative phrase)",
            "expected_events": 1 if lbl == "positive" else 0,
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
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[ERROR] Malformed manifest.json: {exc}", file=sys.stderr)
            sys.exit(1)
        session_id = manifest.get("session_id", dir_path.name)
        speaker_id = manifest.get("speaker_id", "unknown")
        speaker_label = manifest.get("speaker_label", args.speaker or "child")
        split = manifest.get("split", "pilot")
        lbl = manifest.get("label", "positive")
        expected_events = manifest.get("expected_events", 1 if lbl == "positive" else 0)
        distance_m = manifest.get("distance_m", 1.0)
        condition = manifest.get("condition", "quiet_normal_voice")
        expected_phrase = manifest.get("expected_phrase", config.wake_word)

        for idx, clip in enumerate(manifest.get("clips", []), start=1):
            clip_file = dir_path / clip["file"]
            rel_path = str(clip_file.relative_to(ROOT) if clip_file.is_relative_to(ROOT) else clip_file)
            raw_samples.append({
                "sample_id": f"{session_id}-t{idx:02d}",
                "source": rel_path,
                "source_sha256": clip.get("sha256", ""),
                "speaker_id": speaker_id,
                "speaker_label": speaker_label,
                "session_id": session_id,
                "split": split,
                "label": lbl,
                "transcript_human": expected_phrase,
                "expected_events": expected_events,
                "condition": condition,
                "distance_m": distance_m,
                "speaker_confirmed": manifest.get("speaker_confirmed", False),
                "review_status": manifest.get("status", "captured_pending_review"),
            })
    else:
        try:
            raw_samples = load_labels(LABELS_FILE)
        except Exception as exc:
            print(f"[ERROR] Không thể đọc {LABELS_FILE}: {exc}", file=sys.stderr)
            sys.exit(1)

    eligible_samples, ineligible = filter_samples(
        raw_samples,
        split=args.split,
        session_id=args.session_id,
        speaker=args.speaker,
        label=args.label,
        allow_unreviewed=args.allow_unreviewed,
        root=ROOT,
    )

    if not eligible_samples:
        print(
            f"[ERROR] Không có mẫu nào thỏa mãn điều kiện lọc và tiêu chuẩn benchmark "
            f"(split={args.split}, session={args.session_id}, speaker={args.speaker}, label={args.label}, mode={evaluation_mode}).",
            file=sys.stderr,
        )
        if ineligible:
            print(
                f"Tổng cộng {len(ineligible)} mẫu bị bỏ qua do chưa đạt tiêu chuẩn benchmark chính thức:",
                file=sys.stderr,
            )
            for item, reason in ineligible[:5]:
                print(f"  - {item.get('sample_id')}: {reason}", file=sys.stderr)
            if len(ineligible) > 5:
                print(f"  ... và {len(ineligible) - 5} mẫu khác.", file=sys.stderr)
            print("Dùng cờ --allow-unreviewed nếu muốn đánh giá exploratory không chính thức.", file=sys.stderr)
        sys.exit(2)

    print(
        f"[RUNNER] Đang đánh giá {len(eligible_samples)} mẫu WAV trên profile: {', '.join(profiles)} "
        f"(mode={evaluation_mode}, split={args.split})...",
        flush=True,
    )
    results = evaluate_dataset(
        eligible_samples,
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
        sys.exit(1)

    md_report = format_evaluation_markdown(results)

    print(f"\n[SAVED] Đã lưu kết quả JSON: {out_file}", flush=True)
    print(f"[SAVED] Đã lưu báo cáo Markdown: {out_file.with_suffix('.md')}\n", flush=True)
    print(md_report, flush=True)

    has_errors = any(m.get("errors", 0) > 0 for m in results["metrics"].values())
    if has_errors:
        print(
            "[WARN] Quá trình đánh giá có mẫu gặp lỗi xử lý hoặc integrity error. Xem báo cáo chi tiết.",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
