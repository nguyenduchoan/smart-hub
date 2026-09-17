#!/usr/bin/env python3
"""Công cụ duyệt, nghe lại và quản lý nhãn dữ liệu giọng trẻ em (CV-02, CV-06)."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smart_hub.audio import RATE, pcm_stats
from smart_hub.config import load_config
from smart_hub.child_study import (
    CHILD_STUDY_DIR,
    LABELS_FILE,
    SESSIONS_FILE,
    ensure_child_study_dirs,
    load_labels,
    save_label,
    load_sessions,
    save_session,
    compute_file_sha256,
)


def print_summary():
    labels = load_labels()
    sessions = load_sessions()

    print(f"=== TỔNG QUAN BỘ DỮ LIỆU GIỌNG BÉ / NGƯỜI LỚN ===")
    print(f"Thư mục lưu trữ: {CHILD_STUDY_DIR}")
    print(f"Số phiên thu (sessions): {len(sessions)}")
    print(f"Tổng số mẫu WAV đã ghi nhận: {len(labels)}\n")

    if not labels:
        print("Chưa có mẫu nào được ghi nhận trong labels.jsonl.")
        return

    # Counts by split, label, review_status
    splits = {}
    for l in labels:
        s = l.get("split", "unknown")
        lbl = l.get("label", "unknown")
        rev = l.get("review_status", "pending")
        spk = l.get("speaker_id", "unknown")

        key = (s, spk, lbl)
        splits[key] = splits.get(key, 0) + 1

    print(f"{'Split':<8} | {'Speaker':<10} | {'Nhãn':<10} | {'Số lượng':<8}")
    print("-" * 45)
    for (s, spk, lbl), count in sorted(splits.items()):
        print(f"{s:<8} | {spk:<10} | {lbl:<10} | {count:<8}")

    print("\nTrạng thái duyệt:")
    statuses = {}
    for l in labels:
        st = l.get("review_status", "unknown")
        statuses[st] = statuses.get(st, 0) + 1
    for st, c in sorted(statuses.items()):
        print(f"- {st}: {c} mẫu")


def review_session(target_dir=None, target_session=None, auto_accept=False, reviewer="QC"):
    config = load_config()
    labels = load_labels()
    sessions = load_sessions()

    # Find matching labels
    matching = []
    for l in labels:
        if target_session and l.get("session_id") == target_session:
            matching.append(l)
        elif target_dir and str(l.get("source", "")).startswith(str(target_dir)):
            matching.append(l)

    if not matching and target_dir:
        # Check if dir has manifest.json not yet in labels
        dir_path = Path(target_dir) if Path(target_dir).is_absolute() else (ROOT / target_dir)
        manifest_file = dir_path / "manifest.json"
        if manifest_file.exists():
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            rel_dir = str(dir_path.relative_to(ROOT))
            session_id = manifest.get("session_id", dir_path.name)
            speaker_id = manifest.get("speaker_id", f"{manifest.get('speaker_label', 'child')}_01")
            split = manifest.get("split", "pilot")
            label = manifest.get("label", "positive")
            expected_phrase = manifest.get("expected_phrase", config.wake_word)
            expected_events = manifest.get("expected_events", 1 if label == "positive" else 0)

            for idx, clip in enumerate(manifest.get("clips", []), start=1):
                sample_id = f"{speaker_id}-{session_id}-t{idx:02d}"
                wav_path = rel_dir + "/" + clip["file"]
                entry = {
                    "sample_id": sample_id,
                    "source": wav_path,
                    "source_sha256": clip.get("sha256", ""),
                    "speaker_id": speaker_id,
                    "speaker_confirmed": False,
                    "session_id": session_id,
                    "split": split,
                    "label": label,
                    "transcript_human": expected_phrase,
                    "expected_events": expected_events,
                    "distance_m": manifest.get("distance_m", 1.0),
                    "condition": manifest.get("condition", "quiet_normal_voice"),
                    "review_status": "captured_pending_review",
                    "review_note": "Import từ manifest",
                }
                matching.append(entry)

    if not matching:
        print("[INFO] Không tìm thấy mẫu nào cần duyệt.")
        return

    print(f"=== BẮT ĐẦU DUYỆT {len(matching)} MẪU ===")
    for idx, item in enumerate(matching, start=1):
        wav_full = ROOT / item["source"]
        print(f"\n[{idx}/{len(matching)}] Mẫu: {item['sample_id']}")
        print(f"  Source: {item['source']}")
        print(f"  Người nói: {item['speaker_id']} | Nhãn: {item['label']} | Câu: '{item['transcript_human']}'")

        if not wav_full.exists():
            print(f"  [ERROR] Không tìm thấy file {wav_full}!")
            item["review_status"] = "rejected"
            item["review_note"] = "File audio không tồn tại"
            save_label(item)
            continue

        # Inspect WAV quality
        with wave.open(str(wav_full), "rb") as w:
            nframes = w.getnframes()
            pcm = w.readframes(nframes)
            seconds = nframes / RATE
        stats = pcm_stats(pcm)
        print(f"  Thời lượng: {seconds:.2f}s | Peak: {stats['peak']}/32768 | RMS: {stats['rms']} | Clipping: {stats['clipped_percent']}%")

        if auto_accept:
            item["speaker_confirmed"] = True
            item["review_status"] = "accepted"
            item["review_note"] = f"Duyệt tự động bởi {reviewer}"
            save_label(item)
            print("  -> Đã đánh dấu: ACCEPTED (speaker_confirmed=True)")
            continue

        # Interactive options
        while True:
            choice = input("  Lệnh [p: phát loa, a: chấp nhận (accept), r: từ chối (reject), n: cần xem lại (needs_review), s: bỏ qua]: ").strip().lower()
            if choice == "p":
                print(f"  Đang phát qua loa ({config.playback_device})...")
                subprocess.run(["aplay", "-q", "-D", config.playback_device, str(wav_full)])
            elif choice == "a":
                note = input("  Ghi chú (Enter nếu để trống): ").strip() or "Đủ đầu câu và âm ơi; tín hiệu tốt"
                item["speaker_confirmed"] = True
                item["review_status"] = "accepted"
                item["review_note"] = note
                save_label(item)
                print("  -> Đã cập nhật: ACCEPTED")
                break
            elif choice == "r":
                reason = input("  Lý do từ chối (clipping, mất âm, sai người, ...): ").strip() or "Không đạt chất lượng"
                item["speaker_confirmed"] = False
                item["review_status"] = "rejected"
                item["review_note"] = reason
                save_label(item)
                print("  -> Đã cập nhật: REJECTED")
                break
            elif choice == "n":
                note = input("  Ghi chú nghi ngờ: ").strip() or "Cần đối chiếu thêm"
                item["review_status"] = "needs_review"
                item["review_note"] = note
                save_label(item)
                print("  -> Đã cập nhật: NEEDS_REVIEW")
                break
            elif choice == "s":
                print("  -> Bỏ qua")
                break

    print("\n[DONE] Hoàn tất quá trình duyệt!")


def main():
    parser = argparse.ArgumentParser(description="Công cụ kiểm tra, duyệt và nghe lại mẫu thu giọng (CV-02, CV-06).")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("summary", help="Hiển thị tổng quan bộ dữ liệu đã thu và đã duyệt.")

    rev = sub.add_parser("review", help="Duyệt từng mẫu trong phiên hoặc thư mục.")
    rev.add_argument("--session-id", default=None, help="Mã phiên (session_id) cần duyệt.")
    rev.add_argument("--dir", default=None, help="Đường dẫn thư mục thu âm.")
    rev.add_argument("--auto-accept", action="store_true", help="Chấp nhận toàn bộ các mẫu hợp lệ không cần xác nhận từng file.")
    rev.add_argument("--reviewer", default="QC", help="Tên người duyệt.")

    args = parser.parse_args()
    ensure_child_study_dirs()

    if args.command == "review":
        review_session(target_dir=args.dir, target_session=args.session_id,
                       auto_accept=args.auto_accept, reviewer=args.reviewer)
    else:
        print_summary()


if __name__ == "__main__":
    main()
