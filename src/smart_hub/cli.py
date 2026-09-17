import argparse
from contextlib import closing, nullcontext
from datetime import datetime
import json
import math
from pathlib import Path
import signal
import subprocess
import sys
import time

from .audio import (AlsaCapture, AudioError, FRAME_BYTES, FRAME_SAMPLES, RATE,
                    capture_pcm, list_inputs, pcm_stats, wav_frames)
from .config import ROOT, load_config
from .events import WakeEvent, WakeGate


def status(text):
    print(text, file=sys.stderr, flush=True)


def emit(event):
    print(event.line(), flush=True)


def emit_diagnostic(event):
    emit(event)
    status(f"[EVENT] audio={event.audio_seconds:.2f}s score={event.score:.3f}")


def check_microphone(config, seconds=3):
    try:
        devices = list_inputs()
        print("TEST 1 PASS: " + " | ".join(devices), flush=True)
    except (AudioError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"TEST 1 FAIL: {exc}", flush=True)
        print("TEST 2 FAIL: chưa có microphone truy cập được.", flush=True)
        return False
    try:
        data = capture_pcm(config.device, seconds)
        stats = pcm_stats(data)
        if stats["clipped_percent"] > 1:
            raise AudioError(f"Audio clipping > 1%: {stats}; kiểm tra gain/mic boost.")
        if stats["rms"] < 5:
            raise AudioError(f"Audio gần như im lặng: {stats}; kiểm tra mute/input.")
        print("TEST 2 PASS: " + json.dumps(stats, ensure_ascii=False), flush=True)
        return True
    except (AudioError, OSError, ValueError) as exc:
        print(f"TEST 2 FAIL: {exc}", flush=True)
        return False


def mock_run(config):
    status(f"[MOCK] Giả lập score cho ‘{config.wake_word}’; không xác nhận nhận diện giọng thật.")
    gate = WakeGate(config.threshold, release_threshold=0,
                    release_seconds=config.silence_seconds,
                    cooldown_seconds=config.cooldown_seconds)
    # Two long activations; no microphone, engine model or numpy import.
    now = 0.0
    count = 0
    for score, duration in [(0, 1), (1, 5), (0, config.cooldown_seconds + config.silence_seconds + 1), (1, 5), (0, 1)]:
        for _ in range(math.ceil(duration / 0.02)):
            now += 0.02
            if gate.update(score, now):
                emit(WakeEvent(config.wake_word, score, now, datetime.now().astimezone()))
                count += 1
    status(f"[MOCK] {'PASS' if count == 2 else 'FAIL'}: {count}/2 event.")
    return 0 if count == 2 else 1


def calibrate(device):
    from .engine import noise_level
    status("[AUDIO] Giữ yên lặng: ổn định mic 2 giây rồi đo tiếng nền 1 giây.")
    pcm = capture_pcm(device, 1)
    stats = pcm_stats(pcm)
    if stats["clipped_percent"] > 1:
        raise AudioError(
            f"Audio clipping {stats['clipped_percent']:.3f}% (>1%) trên {device!r}; "
            f"RMS={stats['rms']}, peak={stats['peak']}. "
            "Chạy check-mic; kiểm tra volume đầu vào PipeWire và Capture/Mic Boost. "
            "Xem mục ‘Nếu listen báo Audio clipping’ trong README."
        )
    noise = noise_level(pcm)
    status(f"[AUDIO] RMS tiếng nền: {noise:.1f}")
    return noise


def enroll(config, args):
    from .engine import SpeechSegmenter, save_enrollment
    if config.model_path.exists():
        raise ValueError(f"Model đã tồn tại: {config.model_path}. Dùng config khác để thu bộ mới.")
    list_inputs()
    noise = calibrate(config.device)
    clips = []
    if args.fixed_windows:
        import numpy as np
        from .neural import WINDOW_SAMPLES
        if config.engine != "neural":
            raise ValueError("fixed-windows chỉ dùng cho neural.")
        with AlsaCapture(config.device) as source:
            for take in range(1, args.takes + 1):
                status(f"[MẪU {take}/{args.takes}] Nói ‘{config.wake_word}’ MỘT LẦN trong {args.seconds:g}s.")
                pcm = b"".join(source.read_frame() for _ in range(math.ceil(args.seconds * RATE / FRAME_SAMPLES)))
                samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
                # Find the most energetic 0.7s inside a bounded user-labelled take.
                # Capture context too; enrollment is no longer gated by an absolute VAD threshold.
                energy = np.mean(samples[:len(samples) // FRAME_SAMPLES * FRAME_SAMPLES].reshape(-1, FRAME_SAMPLES) ** 2, axis=1)
                average = np.convolve(energy, np.ones(35) / 35, mode="valid")
                best = int(np.argmax(average))
                if float(np.sqrt(average[best])) < max(config.min_rms, noise * 1.3):
                    raise AudioError(f"Mẫu {take}: giọng chưa nổi trên tiếng nền; chưa lưu model.")
                center = (best + 17) * FRAME_SAMPLES
                start = max(0, min(center - WINDOW_SAMPLES // 2, len(samples) - WINDOW_SAMPLES))
                clip = samples[start:start + WINDOW_SAMPLES].astype("<i2").tobytes()
                if pcm_stats(clip)["clipped_percent"] > 1:
                    raise AudioError("Mẫu clipping; chưa lưu.")
                clips.append(clip)
                status(f"[MẪU {take}] Đã lấy cửa sổ 1.5s, RMS nổi bật {np.sqrt(average[best]):.1f}.")
        save_enrollment(config, clips)
        status(f"[ENROLL] Đã lưu {len(clips)} mẫu neural; cần kiểm thử với lời nói mới.")
        return 0
    if args.continuous:
        segmenter = SpeechSegmenter(config, noise)
        status("[AUDIO] Đang ổn định mic 2 giây; chưa nói.")
        with AlsaCapture(config.device) as source:
            status(f"[THU LIÊN TỤC] Nói ‘{config.wake_word}’ {args.takes} lần, cách nhau 3 giây. Cửa sổ {args.seconds:g}s.")
            for _ in range(math.ceil(args.seconds * RATE / FRAME_SAMPLES)):
                segment = segmenter.feed(source.read_frame())
                if segment is None:
                    continue
                if segment.clipped_fraction > 0.01:
                    raise AudioError("Mẫu clipping > 1%; chưa lưu model.")
                clips.append(segment.pcm)
                status(f"[MẪU {len(clips)}/{args.takes}] Đã nhận {len(segment.pcm) / (RATE * 2):.2f}s.")
                if len(clips) == args.takes:
                    break
        if len(clips) != args.takes:
            raise AudioError(f"Chỉ thu được {len(clips)}/{args.takes} mẫu; chưa lưu model.")
        save_enrollment(config, clips)
        status(f"[ENROLL] Đã lưu {len(clips)} mẫu vào {config.model_path}; cần kiểm thử lời nói mới.")
        return 0
    for take in range(1, args.takes + 1):
        if not args.auto:
            input(f"Mẫu {take}/{args.takes}: Enter khi sẵn sàng nói ‘{config.wake_word}’...")
        status("[AUDIO] Đang ổn định mic 2 giây; chưa nói.")
        segmenter = SpeechSegmenter(config, noise)
        segments = []
        with AlsaCapture(config.device) as source:
            status(f"[THU MẪU {take}/{args.takes}] NÓI ‘{config.wake_word}’ MỘT LẦN, rồi im lặng ({args.seconds:g} giây).")
            for _ in range(math.ceil(args.seconds * RATE / FRAME_SAMPLES)):
                segment = segmenter.feed(source.read_frame())
                if segment is not None:
                    segments.append(segment)
        if len(segments) != 1:
            raise AudioError(f"Mẫu {take}: cần đúng một lượt nói, nhận {len(segments)}. "
                             f"Ngưỡng RMS={segmenter.floor:.1f}, đỉnh RMS={segmenter.max_rms:.1f}. "
                             "Chưa lưu model; thu lại.")
        if segments[0].clipped_fraction > 0.01:
            raise AudioError(f"Mẫu {take} clipping > 1%; chưa lưu model.")
        clips.append(segments[0].pcm)
        status(f"[MẪU {take}] Đã nhận {len(clips[-1]) / (RATE * 2):.2f} giây; chưa kiểm chứng nội dung lời nói.")
        if args.auto:
            time.sleep(1)
    save_enrollment(config, clips)
    status(f"[ENROLL] Đã lưu đặc trưng của {len(clips)} mẫu vào {config.model_path}.")
    status("Cần validate-live bằng các lần nói mới. Không lưu audio thô.")
    return 0


def listen(config, args):
    if args.mock:
        return mock_run(config)
    from .detector import create_detector
    from .engine import create_engine
    from .feedback import VoiceFeedback, feed_audio
    engine = create_engine(config)
    event_sink = emit_diagnostic if args.diagnostic else emit
    detector = None
    try:
        if args.wav:
            detector = create_detector(config, engine, event_sink)
            for frame in wav_frames(args.wav):
                detector.feed(frame)
        else:
            noise = calibrate(config.device)
            context = closing(VoiceFeedback(config.feedback_path, config.playback_device)) if config.feedback_enabled else nullcontext(None)
            with context as player:
                def on_wake(event):
                    event_sink(event)
                    if player:
                        player.play()
                detector = create_detector(config, engine, on_wake, noise)
                with AlsaCapture(config.device) as source:
                    status(f"[READY] Đang nghe ‘{config.wake_word}’. Ctrl+C để dừng.")
                    deadline = time.monotonic() + args.seconds if args.seconds else math.inf
                    while time.monotonic() < deadline:
                        feed_audio(detector, source.read_frame(), player)
    finally:
        if detector is not None:
            status(f"[STOP] {detector.events} wake event; score cao nhất {detector.max_score:.3f}; "
                   f"loại {detector.rejected_clipping} lượt bị clipping.")
            if args.diagnostic:
                diagnostic_scores(detector)
    if args.expect_events is not None:
        passed = detector.events == args.expect_events
        status(f"[TEST] {'PASS' if passed else 'FAIL'}: {detector.events}/{args.expect_events} event mong đợi.")
        return 0 if passed else 1
    return 0


def diagnostic_scores(detector):
    if hasattr(detector, "max_positive"):
        status(f"[SCORES] positive_max={detector.max_positive:.3f}, "
               f"negative_cùng_cửa_sổ={detector.negative_at_max_positive:.3f}; "
               f"{detector.scored_windows} cửa sổ đã chấm, "
               f"{detector.below_rms_windows} cửa sổ dưới min_rms, max_rms={detector.max_rms:.1f}.")


def validate_live(config, args):
    """Numbered acceptance results; hardware/model gaps always return failure."""
    from .detector import create_detector
    from .engine import create_engine
    from .feedback import VoiceFeedback, feed_audio
    microphone_ok = check_microphone(config)
    try:
        engine = create_engine(config)
        print(f"TEST 3 PASS: engine mở model ‘{config.wake_word}’, {len(engine.templates)} mẫu.", flush=True)
    except (OSError, ValueError, KeyError) as exc:
        print(f"TEST 3 FAIL: {exc}", flush=True)
        print("TEST 4 FAIL: thiếu model, chưa thể thử nhận diện lời nói thật.", flush=True)
        print("TEST 5 FAIL: chưa thể kiểm chứng số event trên lời nói thật; xem test mock riêng.", flush=True)
        return 1
    if not microphone_ok:
        print("TEST 4 FAIL: microphone chưa đạt.", flush=True)
        print("TEST 5 FAIL: microphone chưa đạt.", flush=True)
        return 1
    noise = calibrate(config.device)
    counts = []
    context = closing(VoiceFeedback(config.feedback_path, config.playback_device)) if config.feedback_enabled else nullcontext(None)
    with context as player:
        def on_wake(event):
            emit(event)
            if player:
                player.play()
        detector = create_detector(config, engine, on_wake, noise)
        for take in range(1, args.attempts + 1):
            if not args.auto:
                input(f"Kiểm thử mới {take}/{args.attempts}: Enter khi sẵn sàng...")
            status("[AUDIO] Đang ổn định mic 2 giây; chưa nói.")
            before = detector.events
            detector.max_score = 0
            if hasattr(detector, "reset_metrics"):
                detector.reset_metrics()
            with AlsaCapture(config.device) as source:
                status(f"[THỬ {take}/{args.attempts}] NÓI ‘{config.wake_word}’ MỘT LẦN rồi im lặng ({args.seconds:g}s).")
                for _ in range(math.ceil(args.seconds * RATE / FRAME_SAMPLES)):
                    feed_audio(detector, source.read_frame(), player)
            counts.append(detector.events - before)
            status(f"[THỬ {take}] events={counts[-1]}, max_score={detector.max_score:.3f}")
            diagnostic_scores(detector)
            if args.auto:
                time.sleep(1)
    detected = all(count >= 1 for count in counts)
    unique = all(count == 1 for count in counts)
    print(f"TEST 4 {'PASS' if detected else 'FAIL'}: số event từng lượt nói mới: {counts}", flush=True)
    print(f"TEST 5 {'PASS' if unique else 'FAIL'}: cần đúng 1 event mỗi lượt, thực tế: {counts}", flush=True)
    status("Kết quả dựa trên việc bạn nói đúng một lần mỗi cửa sổ. Cần thử thêm câu khác và tiếng nền để đánh giá báo nhầm.")
    return 0 if detected and unique else 1


def enroll_negative(config, args):
    import numpy as np
    from .neural import Embedder, MODEL_SHA256, WINDOW_SAMPLES, STEP_SAMPLES
    if config.engine != "neural":
        raise ValueError("Mẫu âm tính chỉ dùng với engine neural.")
    if config.negative_path.exists():
        raise FileExistsError(f"Không ghi đè: {config.negative_path}")
    embedder = Embedder(config.backbone_path)
    vectors = []
    buffer = bytearray()
    accumulated = 0
    with AlsaCapture(config.device) as source:
        status(f"[ÂM TÍNH] Trong {args.seconds:g}s: tạo tiếng nền/nói câu khác, KHÔNG nói ‘{config.wake_word}’.")
        for _ in range(math.ceil(args.seconds * RATE / FRAME_SAMPLES)):
            frame = source.read_frame()
            buffer.extend(frame)
            del buffer[:-WINDOW_SAMPLES * 2]
            accumulated += FRAME_SAMPLES
            if len(buffer) < WINDOW_SAMPLES * 2 or accumulated < STEP_SAMPLES:
                continue
            accumulated = 0
            samples = np.frombuffer(buffer, dtype="<i2").astype(np.float32) / 32768
            if float(np.mean(np.abs(samples) >= 32767 / 32768)) > 0.01:
                raise AudioError("Mẫu âm tính clipping; chưa lưu.")
            vectors.append(embedder.embed(samples))
    if len(vectors) < 10:
        raise ValueError("Cần ít nhất 4 giây âm tính.")
    config.negative_path.parent.mkdir(parents=True, exist_ok=True)
    with config.negative_path.open("xb") as output:
        np.savez_compressed(output, format_version=2, wake_word=config.wake_word,
                            backbone_sha256=MODEL_SHA256, negatives=np.asarray(vectors))
    status(f"[PASS] Đã lưu {len(vectors)} embedding âm tính. Cần kiểm thử mới sau khi thêm bộ lọc.")
    return 0


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 300:
        raise argparse.ArgumentTypeError("Cần 0 < seconds <= 300.")
    return number


def listening_seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 86400:
        raise argparse.ArgumentTypeError("Cần 0 < seconds <= 86400.")
    return number


def command_seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 30:
        raise argparse.ArgumentTypeError("Cần 0 < command-seconds <= 30.")
    return number


def reply_guard_seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 2:
        raise argparse.ArgumentTypeError("Cần 0 <= reply-guard <= 2 giây.")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="Smart Hub: wake word local trên CPU.")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-mic", help="Liệt kê input và kiểm tra capture/clipping.")
    check.add_argument("--seconds", type=positive_seconds, default=3)
    learn = sub.add_parser("enroll", help="Thu mẫu wake word tiếng Việt local.")
    learn.add_argument("--takes", type=int, choices=range(3, 11), default=5)
    learn.add_argument("--seconds", type=positive_seconds, default=4)
    learn.add_argument("--auto", action="store_true", help="Thu liên tiếp, không chờ Enter.")
    learn.add_argument("--continuous", action="store_true", help="Thu đủ số lượt nói trong một cửa sổ liên tục.")
    learn.add_argument("--fixed-windows", action="store_true", help="Mỗi lượt một cửa sổ cố định, giảm phụ thuộc VAD lúc thu mẫu.")
    live = sub.add_parser("listen", help="Nghe liên tục và chỉ in wake event.")
    sources = live.add_mutually_exclusive_group()
    sources.add_argument("--mock", action="store_true")
    sources.add_argument("--wav", type=Path)
    live.add_argument("--seconds", type=positive_seconds)
    live.add_argument("--expect-events", type=int, help="Trả mã lỗi nếu số event không khớp (ví dụ 0 cho test âm tính).")
    live.add_argument("--diagnostic", action="store_true", help="In thêm score của event trên stderr.")
    stt = sub.add_parser("listen-stt", help="Thử wake bằng STT tiếng Việt local trên CPU; không cần enrollment.")
    stt_sources = stt.add_mutually_exclusive_group()
    stt_sources.add_argument("--mock", action="store_true")
    stt_sources.add_argument("--wav", type=Path)
    stt.add_argument("--seconds", type=listening_seconds)
    stt.add_argument("--expect-events", type=int)
    stt.add_argument("--diagnostic", action="store_true", help="In thời gian STT; không in nội dung lời nói.")
    stt.add_argument("--show-text", action="store_true", help="Hiện transcript local trên stderr để tự kiểm tra; không lưu file.")
    stt.add_argument("--alias", action="append", default=[], help="Cách viết khác của đủ cụm gọi, ví dụ 'mai ca ơi'.")
    stt.add_argument("--no-feedback", action="store_true", help="Tắt loa chỉ trong phiên kiểm thử này.")
    assistant = sub.add_parser("assistant", help="Wake STT -> đáp cố định -> log một câu lệnh -> chờ wake.")
    assistant.add_argument("--mock", action="store_true", help="Ba chu kỳ giả lập, không cần mic/model/loa.")
    assistant.add_argument("--seconds", type=listening_seconds)
    assistant.add_argument("--expect-events", type=int)
    assistant.add_argument("--diagnostic", action="store_true", help="Hiện thêm trạng thái và thống kê buffer.")
    assistant.add_argument("--wake-profile", choices=("standard", "sensitive"), default="standard",
                           help="standard giữ mức cũ; sensitive thử giọng nhỏ/ngắn (mặc định standard).")
    assistant.add_argument("--show-wake-text", action="store_true",
                           help="Hiện chữ STT trước wake để kiểm tra giọng chưa nhận; có thể gồm hội thoại nền.")
    assistant.add_argument("--reply-guard", type=reply_guard_seconds, default=0.1,
                           help="Chờ tiếng vọng sau khi phát đáp, 0–2 giây (mặc định 0.1).")
    assistant.add_argument("--buffer-ms", type=int, choices=range(20, 501, 20), default=500,
                           help="PCM chờ xử lý tối đa, 20–500 ms (mặc định 500).")
    assistant.add_argument("--no-feedback", action="store_true", help="Tắt loa chỉ trong phiên kiểm thử này.")
    assistant.add_argument("--command-seconds", type=command_seconds, default=8.0,
                           help="Cửa sổ nghe một câu lệnh sau tiếng đáp, tối đa 30 giây (mặc định 8).")
    assistant_mode = assistant.add_mutually_exclusive_group()
    assistant_mode.add_argument("--wake-only", action="store_true", help="Chỉ wake và đáp; tắt bước nghe/log lệnh.")
    assistant_mode.add_argument("--capture-only", action="store_true",
                                help="Thu trọn một lượt nói sau wake; tự chốt khi im lặng và in thời lượng.")
    assistant.add_argument("--debug-recordings", action="store_true",
                           help="Lưu WAV lượt hoàn chỉnh vào recordings/; chỉ dùng cùng --capture-only.")
    verify = sub.add_parser("validate-live", help="Chạy TEST 1–5 với microphone và lời nói mới.")
    verify.add_argument("--attempts", type=int, choices=range(1, 11), default=3)
    verify.add_argument("--seconds", type=positive_seconds, default=5)
    verify.add_argument("--auto", action="store_true")
    negative = sub.add_parser("enroll-negative", help="Thu tiếng nền và câu khác để giảm báo nhầm.")
    negative.add_argument("--seconds", type=positive_seconds, default=30)
    sub.add_parser("test-feedback", help="Phát thử tiếng phản hồi local, không mở microphone.")
    args = parser.parse_args(argv)
    if args.command == "enroll" and args.fixed_windows and args.seconds < 2:
        parser.error("--fixed-windows cần --seconds >= 2.")
    if args.command in ("listen", "listen-stt", "assistant") and args.expect_events is not None and args.expect_events < 0:
        parser.error("--expect-events phải >= 0.")
    if args.command == "listen-stt" and args.wav and args.seconds is not None:
        parser.error("--wav đọc hết file; không kết hợp --seconds.")
    if args.command == "assistant" and args.debug_recordings and not args.capture_only:
        parser.error("--debug-recordings cần --capture-only.")
    if args.command == "assistant" and args.debug_recordings and args.mock:
        parser.error("--mock không ghi file audio; bỏ --debug-recordings.")

    def stop(*_):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        config = load_config(args.config)
        if args.command == "check-mic":
            return 0 if check_microphone(config, args.seconds) else 1
        if args.command == "enroll":
            return enroll(config, args)
        if args.command == "listen":
            return listen(config, args)
        if args.command == "listen-stt":
            from .stt_wake import run
            return run(config, args, emit, status)
        if args.command == "assistant":
            from .assistant import run
            return run(config, args, emit, status)
        if args.command == "enroll-negative":
            return enroll_negative(config, args)
        if args.command == "test-feedback":
            from .feedback import VoiceFeedback
            with closing(VoiceFeedback(config.feedback_path, config.playback_device)) as player:
                player.play()
                while player.suppressing():
                    time.sleep(0.02)
            status("[PASS] Đã phát file phản hồi; cần người nghe xác nhận đầu ra loa.")
            return 0
        return validate_live(config, args)
    except KeyboardInterrupt:
        status("[STOP] Đã dừng và đóng capture.")
        if args.command in ("listen", "listen-stt", "assistant"):
            if args.expect_events is not None:
                status("[TEST] INCOMPLETE: bài thử bị ngắt; không tính là PASS.")
                return 130
            return 0
        return 130
    except ImportError as exc:
        hint = ("Chạy .venv/bin/python -m pip install -r requirements-stt.txt."
                if args.command in ("listen-stt", "assistant") else "Chạy scripts/setup_env.py rồi dùng .venv/bin/python.")
        status(f"[FAIL] Thiếu dependency: {exc}. {hint}")
        return 1
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, EOFError, subprocess.TimeoutExpired) as exc:
        status(f"[FAIL] {exc}")
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
