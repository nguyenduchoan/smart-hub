"""Optional STT-based wake trigger; no command processing or dynamic replies."""
from contextlib import closing, nullcontext
import json
import math
import time

from .audio import AudioError, RATE, pcm_stats, wav_frames
from .capture_pump import CapturePump
from .feedback import VoiceFeedback
from .stt_keyword import KeywordTrigger


def mock_run(config, args, emit, status):
    status("[MOCK STT] Transcript giả lập; không mở model/mic/loa.")
    trigger = KeywordTrigger(config.wake_word, emit, args.alias, config.cooldown_seconds)
    trigger.accept("hôm nay trời đẹp", 0, 1)
    trigger.accept(config.wake_word, 1, 4)
    trigger.accept(config.wake_word, 1, 7)  # Duplicate final result.
    trigger.accept(config.wake_word, 2, 4 + config.cooldown_seconds / 2)  # Cooldown.
    trigger.accept(config.wake_word, 3, 4 + config.cooldown_seconds + 1)
    passed = trigger.events == 2 and args.expect_events in (None, 2)
    status(f"[MOCK STT] {'PASS' if passed else 'FAIL'}: {trigger.events}/2 event.")
    return 0 if passed else 1


class STTSession:
    def __init__(self, backend, config, args, emit, status, player=None, health_check=lambda: None):
        self.backend, self.args, self.status = backend, args, status
        self.player, self.health_check = player, health_check
        self.samples = 0
        self.segments = 0
        self.clipped = 0
        self.decode_seconds = 0.0
        self.audio_seconds = 0.0
        self.max_decode_seconds = 0.0
        self.suppressed = False
        self.trigger = KeywordTrigger(config.wake_word, self._wake, args.alias, config.cooldown_seconds)
        self.emit = emit

    def _wake(self, event):
        self.emit(event)
        if self.player:
            self.player.play()

    def process(self, segments):
        import numpy as np
        for samples in segments:
            self.segments += 1
            if float(np.mean(np.abs(samples) >= 32767 / 32768)) > 0.01:
                self.clipped += 1
                continue
            started = time.monotonic()
            text = self.backend.transcribe(samples)
            elapsed = time.monotonic() - started
            self.health_check()  # Never emit from a stream already known to have failed.
            self.decode_seconds += elapsed
            self.audio_seconds += len(samples) / RATE
            self.max_decode_seconds = max(self.max_decode_seconds, elapsed)
            if self.args.show_text:
                self.status("[TEXT] " + json.dumps(text, ensure_ascii=False))
            if self.args.diagnostic:
                self.status(f"[STT] đoạn={self.segments} audio={len(samples) / RATE:.2f}s decode={elapsed:.3f}s")
            if self.trigger.accept(text, self.segments, self.samples / RATE):
                self.backend.reset()
                if self.player:
                    break

    def feed(self, frame):
        import numpy as np
        self.samples += len(frame) // 2
        suppressing = bool(self.player and self.player.suppressing())
        if suppressing:
            if not self.suppressed:
                self.backend.reset()
            self.suppressed = True
            return  # Keep draining PCM; neither VAD nor ASR sees reply/echo audio.
        self.suppressed = False
        samples = np.frombuffer(frame, dtype="<i2").astype(np.int32)
        if float(np.mean(np.abs(samples) >= 32767)) > 0.01:
            self.clipped += 1
            self.backend.reset()
            return
        self.process(self.backend.feed(frame))

    def summary(self):
        rtf = self.decode_seconds / self.audio_seconds if self.audio_seconds else 0
        self.status(f"[STOP STT] {self.trigger.events} wake event; {self.segments} đoạn giọng; "
                    f"clipping={self.clipped}; decode_max={self.max_decode_seconds:.3f}s; RTF={rtf:.3f}.")


def run(config, args, emit, status):
    if args.mock:
        return mock_run(config, args, emit, status)
    from .local_stt import LocalSTT
    wake_profile = getattr(args, "wake_profile", "standard")
    backend = LocalSTT(wake_profile=wake_profile)
    status(f"[ENGINE] STT tiếng Việt + VAD sẵn sàng trên CPU ({backend.startup_seconds:.2f}s).")
    session = None
    interrupted = False
    try:
        if args.wav:
            session = STTSession(backend, config, args, emit, status)
            for frame in wav_frames(args.wav):
                session.feed(frame)
            session.process(backend.flush())
        else:
            context = (closing(VoiceFeedback(config.feedback_path, config.playback_device))
                       if config.feedback_enabled and not args.no_feedback else nullcontext(None))
            with context as player:
                status("[AUDIO] Giữ yên lặng: ổn định mic 2 giây, kiểm tra tín hiệu 1 giây.")
                with CapturePump(config.device) as source:
                    stats = pcm_stats(b"".join(source.read_frame() for _ in range(50)))
                    if stats["clipped_percent"] > 1 or stats["rms"] < 5:
                        raise AudioError(f"Mic clipping hoặc gần như im lặng: {stats}; chạy check-mic.")
                    session = STTSession(backend, config, args, emit, status, player, source.check_health)
                    status(f"[READY STT] Đang nghe ‘{config.wake_word}’. Nói riêng câu gọi, chờ đáp; Ctrl+C để dừng.")
                    deadline = time.monotonic() + args.seconds if args.seconds else math.inf
                    while time.monotonic() < deadline:
                        session.feed(source.read_frame())
                    source.check_health()
                    if player:
                        # Finish a last acknowledgement while continuing to drain capture.
                        while player.suppressing():
                            source.read_frame()
                    if args.diagnostic:
                        status(f"[CAPTURE] queue_peak={source.max_pending}/25 frame.")
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if session:
            session.summary()
    if interrupted:
        status("[STOP] Đã dừng và đóng capture.")
        if args.expect_events is not None:
            status("[TEST] INCOMPLETE: dừng trước khi hết bài thử; chưa nghiệm thu số event.")
            return 130
        return 0
    if args.expect_events is not None:
        passed = session.trigger.events == args.expect_events and session.clipped == 0
        status(f"[TEST] {'PASS' if passed else 'FAIL'}: {session.trigger.events}/{args.expect_events} event; clipping={session.clipped}.")
        return 0 if passed else 1
    return 0
