#!/usr/bin/env python3
"""Review, listen to, and manage child-study labels (CV-02/CV-06)."""
import argparse
from datetime import datetime
import json
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
    ensure_child_study_dirs,
    load_labels,
    save_label,
    load_sessions,
    save_session,
    compute_file_sha256,
)


def verify_audio_file(path, expected_sha=None):
    """Verify original checksum provenance plus WAV technical contract.

    R2 deliberately treats a missing expected checksum as a failed integrity
    check. The current file hash must never be silently backfilled during review.
    """
    p = Path(path)
    if not str(expected_sha or "").strip():
        return False, "Thiếu source_sha256 gốc; không thể xác minh provenance", None
    if not p.is_file():
        return False, f"File audio không tồn tại: {p}", None

    try:
        actual_sha = compute_file_sha256(p)
    except Exception as exc:
        return False, f"Lỗi đọc file: {exc}", None

    if actual_sha.lower() != str(expected_sha).lower():
        return (
            False,
            f"SHA-256 mismatch (kỳ vọng {str(expected_sha)[:12]}..., thực tế {actual_sha[:12]}...)",
            None,
        )

    try:
        with wave.open(str(p), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) != (1, 2, RATE, "NONE"):
                return False, "WAV không đúng chuẩn 16kHz mono 16-bit PCM", None
            nframes = w.getnframes()
            pcm = w.readframes(nframes)
            if len(pcm) != nframes * 2 or not pcm:
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
    print("=== TỔNG QUAN BỘ DỮ LIỆU GIỌNG BÉ / NGƯỜI LỚN ===")
    print(f"Thư mục lưu trữ: {CHILD_STUDY_DIR}")
    print(f"Số phiên thu (sessions): {len(sessions)}")
    print(f"Tổng số mẫu WAV đã ghi nhận: {len(labels)}\n")
    if not labels:
        print("Chưa có mẫu nào được ghi nhận trong labels.jsonl.")
        return
    counts = {}
    for item in labels:
        key = (item.get("split", "unknown"), item.get("speaker_id", "unknown"), item.get("label", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    print(f"{'Split':<8} | {'Speaker':<10} | {'Nhãn':<10} | {'Số lượng':<8}")
    print("-" * 45)
    for (split, speaker, label), count in sorted(counts.items()):
        print(f"{split:<8} | {speaker:<10} | {label:<10} | {count:<8}")
    statuses = {}
    for item in labels:
        status = item.get("review_status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    print("\nTrạng thái duyệt:")
    for status, count in sorted(statuses.items()):
        print(f"- {status}: {count} mẫu")


def update_session_review_status(session_ids, reviewer, sessions_file=None, labels_file=None):
    if not session_ids:
        return
    sessions = load_sessions(sessions_file)
    labels = load_labels(labels_file)
    now_iso = datetime.now().astimezone().isoformat()
    for session_id in session_ids:
        session = next((s for s in sessions if s.get("session_id") == session_id), None)
        if not session:
            continue
        matching = [l for l in labels if l.get("session_id") == session_id]
        accepted = sum(1 for l in matching if l.get("review_status") == "accepted")
        rejected = sum(1 for l in matching if l.get("review_status") == "rejected")
        total = len(matching)
        session["reviewer"] = reviewer
        session["reviewed_at"] = now_iso
        session["status"] = "reviewed" if total and accepted + rejected == total else "partially_reviewed"
        session["notes"] = f"Đã duyệt bởi {reviewer}: {accepted} accepted, {rejected} rejected trên {total} mẫu."
        save_session(session, sessions_file)


def _manifest_entries(target_dir, config):
    dir_path = Path(target_dir) if Path(target_dir).is_absolute() else (ROOT / target_dir)
    manifest_file = dir_path / "manifest.json"
    if not manifest_file.exists():
        return []
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    rel_dir = str(dir_path.relative_to(ROOT))
    session_id = manifest.get("session_id", dir_path.name)
    speaker_id = manifest.get("speaker_id", "unknown")
    speaker_label = manifest.get("speaker_label", "child")
    split = manifest.get("split", "pilot")
    label = manifest.get("label", "positive")
    expected_phrase = manifest.get("expected_phrase", config.wake_word)
    expected_events = manifest.get("expected_events", 1 if label == "positive" else 0)
    entries = []
    for idx, clip in enumerate(manifest.get("clips", []), start=1):
        entries.append({
            "sample_id": f"{speaker_id}-{session_id}-t{idx:02d}",
            "source": rel_dir + "/" + clip["file"],
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
        })
    return entries


def review_session(target_dir=None, target_session=None, auto_qc=False, reviewer="QC"):
    config = load_config()
    labels = load_labels()
    matching = []
    for item in labels:
        if target_session and item.get("session_id") == target_session:
            matching.append(item)
        elif target_dir and str(item.get("source", "")).startswith(str(target_dir)):
            matching.append(item)

    if not matching and target_dir:
        try:
            matching = _manifest_entries(target_dir, config)
        except Exception as exc:
            print(f"[ERROR] Malformed manifest: {exc}", file=sys.stderr)
            return 1

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

        ok, err_msg, stats = verify_audio_file(wav_full, item.get("source_sha256"))
        if ok:
            print(
                f"  Thời lượng: {stats['seconds']:.2f}s | Peak: {stats['peak']}/32768 | "
                f"RMS: {stats['rms']} | Clipping: {stats['clipped_percent']}% | SHA256: OK"
            )
        else:
            print(f"  [ERROR] Lỗi kiểm tra file audio: {err_msg}")

        if auto_qc:
            item["speaker_confirmed"] = False
            item["reviewer"] = reviewer
            item["reviewed_at"] = datetime.now().astimezone().isoformat()
            if ok:
                item["review_status"] = "technical_pass"
                item["review_note"] = f"Technical QC passed (format & original SHA verified) by {reviewer}"
                print("  -> TECHNICAL_PASS; vẫn cần nghe thủ công để xác nhận speaker")
            else:
                item["review_status"] = "needs_review"
                item["review_note"] = f"Technical QC incomplete/failed: {err_msg}"
                print(f"  -> NEEDS_REVIEW ({err_msg})")
            save_label(item)
            reviewed_sessions.add(item.get("session_id"))
            continue

        while True:
            choice = input("  Lệnh [p: phát, a: accept, r: reject, n: needs_review, s: skip]: ").strip().lower()
            if choice == "p":
                if wav_full.exists():
                    subprocess.run(["aplay", "-q", "-D", config.playback_device, str(wav_full)])
                else:
                    print("  [ERROR] File không tồn tại để phát!")
            elif choice == "a":
                if not ok:
                    print(f"  [BLOCK] KHÔNG THỂ ACCEPT: {err_msg}")
                    continue
                note = input("  Ghi chú xác nhận người nói/nội dung: ").strip() or "Đã nghe và xác nhận đúng người nói/nội dung"
                item["speaker_confirmed"] = True
                item["review_status"] = "accepted"
                item["reviewer"] = reviewer
                item["reviewed_at"] = datetime.now().astimezone().isoformat()
                item["review_note"] = note
                save_label(item)
                reviewed_sessions.add(item.get("session_id"))
                print("  -> ACCEPTED (speaker_confirmed=True)")
                break
            elif choice == "r":
                reason = input("  Lý do từ chối: ").strip() or (err_msg if not ok else "Không đạt chất lượng")
                item["speaker_confirmed"] = False
                item["review_status"] = "rejected"
                item["reviewer"] = reviewer
                item["reviewed_at"] = datetime.now().astimezone().isoformat()
                item["review_note"] = reason
                save_label(item)
                reviewed_sessions.add(item.get("session_id"))
                print("  -> REJECTED")
                break
            elif choice == "n":
                item["speaker_confirmed"] = False
                item["review_status"] = "needs_review"
                item["reviewer"] = reviewer
                item["reviewed_at"] = datetime.now().astimezone().isoformat()
                item["review_note"] = input("  Ghi chú: ").strip() or err_msg or "Cần đối chiếu thêm"
                save_label(item)
                reviewed_sessions.add(item.get("session_id"))
                print("  -> NEEDS_REVIEW")
                break
            elif choice == "s":
                print("  -> Bỏ qua")
                break

    update_session_review_status(reviewed_sessions, reviewer)
    print("\n[DONE] Hoàn tất quá trình duyệt!")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Công cụ kiểm tra, duyệt và nghe lại mẫu thu giọng (CV-02, CV-06).")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("summary", help="Hiển thị tổng quan bộ dữ liệu.")
    review = sub.add_parser("review", help="Duyệt từng mẫu trong phiên hoặc thư mục.")
    selector = review.add_mutually_exclusive_group(required=True)
    selector.add_argument("--session-id", default=None, help="Mã phiên cần duyệt.")
    selector.add_argument("--dir", default=None, help="Đường dẫn thư mục thu âm.")
    review.add_argument(
        "--auto-qc", "--auto-accept", dest="auto_qc", action="store_true",
        help="Chỉ QC kỹ thuật; không tự xác nhận người nói.",
    )
    review.add_argument("--reviewer", default="QC", help="Tên người duyệt.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    ensure_child_study_dirs()
    if args.command == "review":
        return review_session(args.dir, args.session_id, args.auto_qc, args.reviewer)
    print_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
