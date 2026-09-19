#!/usr/bin/env python3
"""Explicitly requested local recordings for child and adult voice study.
Never overwrite enrollment/models; strict privacy and local-only storage.
"""
import argparse
from array import array
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smart_hub.audio import AlsaCapture, RATE, pcm_stats
from smart_hub.config import load_config
from smart_hub.locks import audio_lock, ResourceBusyError
from smart_hub.child_study import (
    NEGATIVE_PRESETS,
    create_label_entry,
    save_session_and_labels,
    load_sessions,
    ensure_child_study_dirs,
)


def write_wav(path, pcm):
    with path.open("xb") as file, wave.open(file, "wb") as wav:
        wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)


def positive_int(val):
    ival = int(val)
    if ival <= 0:
        raise argparse.ArgumentTypeError(f"Số lượt thu phải > 0 (nhận {val}).")
    return ival


def main():
    parser = argparse.ArgumentParser(
        description="Thu mẫu giọng trẻ em/người lớn local theo tiếng tít; quản lý nhãn và split (CV-04)."
    )
    parser.add_argument(
        "--speaker",
        required=True,
        choices=("adult", "child"),
        help="Người nói: adult (người lớn) hoặc child (trẻ em).",
    )
    parser.add_argument(
        "--speaker-id",
        default=None,
        help="Mã định danh người nói, ví dụ child_01 hoặc adult_01.",
    )
    parser.add_argument(
        "--takes",
        type=positive_int,
        default=5,
        help="Số lượt thu trong phiên này (mặc định 5).",
    )
    parser.add_argument(
        "--phrase",
        default=None,
        help="Câu nói yêu cầu (mặc định là wake word từ config.json, e.g. 'Maika ơi').",
    )
    parser.add_argument(
        "--preset",
        type=int,
        choices=range(1, 11),
        default=None,
        help="Chọn câu âm tính theo danh sách mục 5 (1: Maika, 2: Mai ca, ..., 10: Em nghe).",
    )
    parser.add_argument(
        "--label",
        choices=("positive", "negative"),
        default=None,
        help="Nhãn câu gọi: positive (dương tính) hoặc negative (âm tính). Tự suy ra nếu để trống.",
    )
    parser.add_argument(
        "--expected-events",
        type=int,
        default=None,
        help="Số event mong đợi: 1 cho dương tính, 0 cho âm tính (tự động theo label).",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Mã phiên thu (e.g. S01, pilot-child-01). Tự sinh nếu để trống.",
    )
    parser.add_argument(
        "--split",
        choices=("pilot", "dev", "test"),
        default="pilot",
        help="Tập dữ liệu: pilot (mặc định), dev (tinh chỉnh) hoặc test (kiểm thử giữ riêng).",
    )
    parser.add_argument(
        "--distance",
        type=float,
        default=1.0,
        help="Khoảng cách từ miệng tới mic tính bằng mét (mặc định 1.0).",
    )
    parser.add_argument(
        "--condition",
        default="quiet_normal_voice",
        help="Điều kiện thu âm (mặc định 'quiet_normal_voice', 'quiet_far', 'normal_noise').",
    )
    parser.add_argument(
        "--manual-advance",
        action="store_true",
        help="Chờ người lớn nhấn Enter trước mỗi lượt nói để bé chuẩn bị thoải mái.",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Không tự động đăng ký phiên vào child-study/sessions.json và labels.jsonl.",
    )

    args = parser.parse_args()
    config = load_config()
    os.umask(0o077)

    # Contract validations (Item 10)
    if args.preset is not None and args.label == "positive":
        parser.error("--preset chỉ áp dụng cho câu âm tính (negative); không thể kết hợp với --label positive.")

    # Resolve speaker ID and speaker label
    speaker_id = args.speaker_id or f"{args.speaker}_01"
    if not speaker_id.strip():
        parser.error("Mã người nói không được để trống.")

    # Resolve phrase and label
    if args.preset is not None:
        phrase = NEGATIVE_PRESETS[args.preset]
        label = "negative"
    elif args.phrase is not None:
        phrase = args.phrase.strip()
        if not phrase:
            parser.error("Câu thu âm (--phrase) không được để trống.")
        label = args.label or ("positive" if phrase.lower() == config.wake_word.lower() else "negative")
    else:
        phrase = config.wake_word
        label = args.label or "positive"

    # Expected events contract
    expected_events = args.expected_events if args.expected_events is not None else (1 if label == "positive" else 0)
    if label == "positive" and expected_events != 1:
        parser.error(f"Nhãn positive yêu cầu expected_events=1 (nhận {expected_events}).")
    if label == "negative" and expected_events != 0:
        parser.error(f"Nhãn negative yêu cầu expected_events=0 (nhận {expected_events}).")
    if expected_events < 0:
        parser.error("Số event mong đợi (--expected-events) phải >= 0.")

    # Distance validation
    if not math.isfinite(args.distance) or args.distance <= 0:
        parser.error("Khoảng cách --distance phải là số dương hữu hạn.")

    # Session ID collision check (Item 4)
    now = datetime.now()
    if args.session_id:
        session_id = args.session_id.strip()
        if not session_id:
            parser.error("Mã phiên thu (--session-id) không được để trống.")
        existing_sessions = load_sessions()
        if any(s.get("session_id") == session_id for s in existing_sessions):
            parser.error(
                f"Mã phiên thu '{session_id}' đã tồn tại trong sessions.json; "
                f"không thể ghi đè để bảo vệ tính toàn vẹn dữ liệu."
            )
    else:
        # Collision-resistant auto-generated session ID
        session_id = f"S_{now.strftime('%Y%m%d_%H%M%S_%f')}_{args.speaker}"

    root = ROOT / "recordings"
    root.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=now.strftime("%Y%m%d-%H%M%S-") + args.speaker + "-", dir=root))

    manifest = {
        "session_id": session_id,
        "split": args.split,
        "speaker_label": args.speaker,
        "speaker_id": speaker_id,
        "label": label,
        "expected_phrase": phrase,
        "expected_events": expected_events,
        "distance_m": args.distance,
        "condition": args.condition,
        "speaker_confirmed": False,
        "created_at": now.astimezone().isoformat(),
        "device": config.device,
        "sample_rate": RATE,
        "status": "incomplete",
        "clips": [],
    }

    def save():
        path = directory / "manifest.json"
        tmp = directory / "manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def sync_to_child_study():
        if args.no_sync:
            return
        ensure_child_study_dirs()
        rel_dir = str(directory.relative_to(ROOT))
        session_record = {
            "session_id": session_id,
            "created_at": manifest["created_at"],
            "speaker_id": speaker_id,
            "speaker_label": args.speaker,
            "purpose": args.split,
            "device": config.device,
            "distance_m": args.distance,
            "condition": args.condition,
            "directory": rel_dir,
            "expected_phrase": phrase,
            "label": label,
            "takes_planned": args.takes,
            "takes_captured": len(manifest["clips"]),
            "status": manifest["status"],
            "reviewer": "pending",
            "notes": f"Thu local bằng record_wake_samples.py ({args.split})",
        }

        label_entries = []
        for idx, clip in enumerate(manifest["clips"], start=1):
            sample_id = f"{speaker_id}-{session_id}-t{idx:02d}"
            wav_path = rel_dir + "/" + clip["file"]
            label_entry = create_label_entry(
                sample_id=sample_id,
                source=wav_path,
                source_sha256=clip["sha256"],
                speaker_id=speaker_id,
                speaker_label=args.speaker,
                session_id=session_id,
                split=args.split,
                label=label,
                transcript_human=phrase,
                expected_events=expected_events,
                distance_m=args.distance,
                condition=args.condition,
                speaker_confirmed=False,
                review_status="captured_pending_review",
                review_note="Mới thu, chờ nghe lại và xác nhận người nói",
            )
            label_entries.append(label_entry)

        save_session_and_labels(session_record, label_entries)

    def handle_signal(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_signal)
    save()
    print(f"[OUTPUT] Thư mục thu âm: {directory}", flush=True)
    print(
        f"[CONFIG] Speaker: {speaker_id} ({args.speaker}) | Split: {args.split} | Label: {label} | Câu: '{phrase}'",
        flush=True,
    )

    try:
        with audio_lock(timeout=0.0):
            with tempfile.TemporaryDirectory(prefix="smart-hub-cue-") as cue_directory:
                cue_path = Path(cue_directory) / "cue.wav"
                count = round(RATE * 0.12)
                values = array(
                    "h",
                    (
                        round(5000 * math.sin(2 * math.pi * 880 * i / RATE) * min(1, i / 160, (count - 1 - i) / 160))
                        for i in range(count)
                    ),
                )
                if sys.byteorder != "little":
                    values.byteswap()
                write_wav(cue_path, values.tobytes())

                print("[PREPARE] Ổn định mic; chuẩn bị sẵn sàng...", flush=True)
                with AlsaCapture(config.device) as capture:
                    # ~2.5 seconds mic stabilization
                    for _ in range(5 * 25):
                        capture.read_frame()

                    for take in range(1, args.takes + 1):
                        if args.manual_advance:
                            input(
                                f"\n[READY {take}/{args.takes}] Bé/Người nói chuẩn bị nói '{phrase}'. Nhấn Enter để bắt đầu tiếng tít..."
                            )
                            capture.drain()

                        print(f"\n[CUE {take}/{args.takes}] Sau tiếng tít, nói ‘{phrase}’ một lần duy nhất.", flush=True)
                        with subprocess.Popen(
                            ["aplay", "-q", "-D", config.playback_device, str(cue_path)],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE,
                        ) as player:
                            started = time.monotonic()
                            while player.poll() is None:
                                capture.read_frame()
                                if time.monotonic() - started > 3:
                                    player.kill()
                                    raise RuntimeError("Tiếng tít không phát xong; đã dừng thu.")
                            if player.returncode:
                                raise RuntimeError(player.stderr.read().decode(errors="replace"))

                        # Tail protection: drop trailing cue echo (0.1s = 5 frames)
                        for _ in range(5):
                            capture.read_frame()

                        recorded_at = datetime.now().astimezone().isoformat()
                        # Capture exactly 5.0 seconds (250 frames of 20 ms)
                        pcm = b"".join(capture.read_frame() for _ in range(5 * 50))
                        name = f"take-{take:02d}.wav"
                        path = directory / name
                        write_wav(path, pcm)
                        stats = pcm_stats(pcm)
                        manifest["clips"].append({
                            "file": name,
                            "recorded_at": recorded_at,
                            "stats": stats,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        })
                        save()
                        print(
                            f"[SAVED {take}/{args.takes}] {name} (5s); peak={stats['peak']}; rms={stats['rms']}; clipping={stats['clipped_percent']}%",
                            flush=True,
                        )

                        if stats["clipped_percent"] > 1:
                            raise RuntimeError("Audio clipping > 1%; dừng để kiểm tra gain, không tự thay đổi gain.")

                        if take < args.takes and not args.manual_advance:
                            for _ in range(75):
                                capture.read_frame()

    except ResourceBusyError as exc:
        manifest["status"] = "interrupted"
        manifest["error"] = f"Microphone đang bận: {exc}"
        save()
        print(
            f"\n[ERROR] Microphone đang được sử dụng bởi tiến trình khác (dashboard/runtime): {exc}.\n"
            f"Vui lòng đợi phiên hiện tại kết thúc hoặc dừng tiến trình đang giữ mic.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
        save()
        try:
            sync_to_child_study()
        except Exception:
            pass
        print(
            f"\n[INTERRUPT] Đã ngắt thu giữa chừng. Đã lưu {len(manifest['clips'])} take(s).",
            file=sys.stderr,
            flush=True,
        )
        raise
    except Exception as exc:
        manifest["status"] = "interrupted"
        save()
        try:
            sync_to_child_study()
        except Exception:
            pass
        print(f"\n[ERROR] Lỗi thu âm: {exc}", file=sys.stderr, flush=True)
        raise

    manifest["status"] = "captured_pending_review"
    save()
    try:
        sync_to_child_study()
    except Exception as exc:
        manifest["status"] = "sync_failed"
        manifest["sync_error"] = str(exc)
        save()
        print(f"\n[ERROR] Lỗi đồng bộ metadata child-study: {exc}", file=sys.stderr, flush=True)
        raise

    print("\n[DONE] Đã thu đủ cửa sổ; cần đối chiếu nội dung và xác nhận người nói.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit as exc:
        sys.exit(exc.code)
    except Exception as exc:
        print(f"[STOP] {str(exc) or 'Đã dừng thu.'}", file=sys.stderr, flush=True)
        sys.exit(1)
