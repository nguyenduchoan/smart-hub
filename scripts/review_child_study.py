#!/usr/bin/env python3
"""Công cụ duyệt, nghe lại và quản lý nhãn dữ liệu giọng trẻ em (CV-02, CV-06)."""
import argparse
from datetime import datetime
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


def verify_audio_file(path, expected_sha=None):
    """Verify audio file exists, has correct 16kHz mono 16-bit PCM format and matches SHA-256.
    Returns (ok, error_message, stats).
    """
    p = Path(path)
    if not p.is_file():
        return False, f"File audio không tồn tại: {p}", None

    try:
        actual_sha = compute_file_sha256(p)
    except Exception as exc:
        return False, f"Lỗi đọc file: {exc}", None

    if not expected_sha or not str(expected_sha).strip():
        return False, "Thiếu source_sha256 gốc trong nhãn", None

    if actual_sha.lower() != str(expected_sha).lower():
        return (
            False,
            f"SHA-256 mismatch (kỳ vọng {expected_sha[:12]}..., thực tế {actual_sha[:12]}...)",
            None,
        )

    try:
        with wave.open(str(p), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) != (1, 2, RATE, "NONE"):
                return False, "WAV không đúng chuẩn 16kHz mono 16-bit PCM", None
            nframes = w.getnframes()
            pcm = w.readframes(nframes)
            if len(pcm) != nframes * 2 or len(pcm) == 0:
                return False, "WAV rỗng hoặc bị cắt cụt", None
            stats = pcm_stats(pcm)
            stats["seconds"] = nframes / RATE
            stats["sha256"] = actual_sha
            return True, "OK", stats
    except Exception as exc:
        return False, f"Lỗi đọc dữ liệu WAV: {exc}", None


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

    splits = {}
    for l in labels:
        s = l.get("split", "unknown")
        lbl = l.get("label", "unknown")
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


def update_session_review_status(session_ids, reviewer, sessions_file=None, labels_file=None):
    """Update sessions.json to reflect that review was conducted by reviewer."""
    if not session_ids:
        return
    sessions = load_sessions(sessions_file)
    all_labels = load_labels(labels_file)

    now_iso = datetime.now().astimezone().isoformat()
    for s_id in session_ids:
        sess = next((s for s in sessions if s.get("session_id") == s_id), None)
        if sess:
            sess_labels = [l for l in all_labels if l.get("session_id") == s_id]
            accepted_cnt = sum(1 for l in sess_labels if l.get("review_status") == "accepted")
            rejected_cnt = sum(1 for l in sess_labels if l.get("review_status") == "rejected")
            total_cnt = len(sess_labels)

            sess["reviewer"] = reviewer
            sess["reviewed_at"] = now_iso
            if accepted_cnt + rejected_cnt == total_cnt and total_cnt > 0:
                sess["status"] = "reviewed"
            else:
                sess["status"] = "partially_reviewed"
            sess["notes"] = (
                f"Đã duyệt bởi {reviewer}: {accepted_cnt} accepted, {rejected_cnt} rejected trên {total_cnt} mẫu."
            )
            save_session(sess, sessions_file)


def review_session(target_dir=None, target_session=None, auto_qc=False, reviewer="QC"):
    config = load_config()
    labels = load_labels()

    if not target_session and not target_dir:
        print("[ERROR] Cần chỉ định --session-id hoặc --dir để chọn mẫu cần duyệt.", file=sys.stderr)
        return 1

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
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[ERROR] Malformed manifest: {exc}", file=sys.stderr)
                return 1
            rel_dir = str(dir_path.relative_to(ROOT))
            session_id = manifest.get("session_id", dir_path.name)
            speaker_id = manifest.get("speaker_id", "unknown")
            speaker_label = manifest.get("speaker_label", "child")
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
                    "speaker_label": speaker_label,
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
        return 0

    reviewed_sessions = set()
    print(f"=== BẮT ĐẦU DUYỆT {len(matching)} MẪU ===")
    for idx, item in enumerate(matching, start=1):
        wav_full = Path(item["source"])
        if not wav_full.is_absolute():
            wav_full = ROOT / wav_full

        print(f"\n[{idx}/{len(matching)}] Mẫu: {item['sample_id']}")
        print(f"  Source: {item['source']}")
        print(f"  Người nói: {item['speaker_id']} ({item.get('speaker_label', 'unknown')}) | Nhãn: {item['label']} | Câu: '{item.get('transcript_human')}'")

        ok, err_msg, stats = verify_audio_file(wav_full, expected_sha=item.get("source_sha256"))

        if not ok:
            print(f"  [ERROR] Lỗi kiểm tra file audio: {err_msg}")
            if auto_qc:
                item["speaker_confirmed"] = False
                item["reviewer"] = reviewer
                item["reviewed_at"] = datetime.now().astimezone().isoformat()
                if not item.get("source_sha256") or not str(item.get("source_sha256")).strip() or "Thiếu source_sha256" in str(err_msg):
                    item["review_status"] = "needs_review"
                    item["review_note"] = "Technical QC incomplete: missing original source_sha256"
                    print(f"  -> Đã cập nhật: NEEDS_REVIEW ({item['review_note']})")
                else:
                    item["review_status"] = "rejected"
                    item["review_note"] = f"Technical QC failed: {err_msg}"
                    print(f"  -> Đã cập nhật: REJECTED ({err_msg})")
                save_label(item)
                reviewed_sessions.add(item.get("session_id"))
                continue
        else:
            print(
                f"  Thời lượng: {stats['seconds']:.2f}s | Peak: {stats['peak']}/32768 | RMS: {stats['rms']} | Clipping: {stats['clipped_percent']}% | SHA256: OK"
            )

        if auto_qc:
            if ok:
                # Auto QC only sets technical_pass; DOES NOT confirm speaker
                item["speaker_confirmed"] = False
                item["review_status"] = "technical_pass"
                item["reviewer"] = reviewer
                item["reviewed_at"] = datetime.now().astimezone().isoformat()
                item["review_note"] = f"Technical QC passed (format & SHA-256 verified) by {reviewer}"
                save_label(item)
                reviewed_sessions.add(item.get("session_id"))
                print("  -> Đã cập nhật: TECHNICAL_PASS (chờ con người nghe và xác nhận người nói để accepted)")
            continue

        # Interactive manual review
        while True:
            choice = input(
                "  Lệnh [p: phát loa, a: chấp nhận (accept), r: từ chối (reject), n: cần xem lại (needs_review), s: bỏ qua]: "
            ).strip().lower()
            if choice == "p":
                if not wav_full.exists():
                    print("  [ERROR] File không tồn tại để phát!")
                else:
                    print(f"  Đang phát qua loa ({config.playback_device})...")
                    subprocess.run(["aplay", "-q", "-D", config.playback_device, str(wav_full)])
            elif choice == "a":
                if not ok or not item.get("source_sha256"):
                    reason = err_msg if not ok else "Thiếu source_sha256 gốc trong nhãn"
                    print(f"  [BLOCK] KHÔNG THỂ CHẤP NHẬN: file audio không đạt kiểm tra kỹ thuật hoặc thiếu SHA gốc ({reason})!")
                    print("  Chỉ có thể chọn 'r' (reject) hoặc 'n' (needs_review).")
                    continue
                note = input("  Ghi chú xác nhận người nói và nội dung (Enter nếu mặc định): ").strip() or "Đủ đầu câu và âm ơi; xác nhận người nói và tín hiệu tốt"
                item["speaker_confirmed"] = True
                item["review_status"] = "accepted"
                item["reviewer"] = reviewer
                item["reviewed_at"] = datetime.now().astimezone().isoformat()
                item["review_note"] = note
                save_label(item)
                reviewed_sessions.add(item.get("session_id"))
                print("  -> Đã cập nhật: ACCEPTED (speaker_confirmed=True)")
                break
            elif choice == "r":
                reason = input("  Lý do từ chối (clipping, mất âm, sai người, sha mismatch...): ").strip() or (err_msg if not ok else "Không đạt chất lượng")
                item["speaker_confirmed"] = False
                item["review_status"] = "rejected"
                item["reviewer"] = reviewer
                item["reviewed_at"] = datetime.now().astimezone().isoformat()
                item["review_note"] = reason
                save_label(item)
                reviewed_sessions.add(item.get("session_id"))
                print("  -> Đã cập nhật: REJECTED")
                break
            elif choice == "n":
                note = input("  Ghi chú nghi ngờ: ").strip() or "Cần đối chiếu thêm"
                item["speaker_confirmed"] = False
                item["review_status"] = "needs_review"
                item["reviewer"] = reviewer
                item["reviewed_at"] = datetime.now().astimezone().isoformat()
                item["review_note"] = note
                save_label(item)
                reviewed_sessions.add(item.get("session_id"))
                print("  -> Đã cập nhật: NEEDS_REVIEW")
                break
            elif choice == "s":
                print("  -> Bỏ qua")
                break

    # Update session review status in sessions.json
    update_session_review_status(reviewed_sessions, reviewer)
    print("\n[DONE] Hoàn tất quá trình duyệt!")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Công cụ kiểm tra, duyệt và nghe lại mẫu thu giọng (CV-02, CV-06).")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("summary", help="Hiển thị tổng quan bộ dữ liệu đã thu và đã duyệt.")

    rev = sub.add_parser("review", help="Duyệt từng mẫu trong phiên hoặc thư mục.")
    selector = rev.add_mutually_exclusive_group(required=True)
    selector.add_argument("--session-id", default=None, help="Mã phiên (session_id) cần duyệt.")
    selector.add_argument("--dir", default=None, help="Đường dẫn thư mục thu âm.")
    rev.add_argument(
        "--auto-qc",
        "--auto-accept",
        dest="auto_qc",
        action="store_true",
        help="Kiểm tra kỹ thuật tự động (format, readable, SHA-256); đặt technical_pass nhưng KHÔNG tự xác nhận người nói.",
    )
    rev.add_argument("--reviewer", default="QC", help="Tên người duyệt.")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    ensure_child_study_dirs()

    if args.command == "review":
        return review_session(
            target_dir=args.dir,
            target_session=args.session_id,
            auto_qc=args.auto_qc,
            reviewer=args.reviewer,
        )
    else:
        print_summary()
        return 0


if __name__ == "__main__":
    sys.exit(main())
