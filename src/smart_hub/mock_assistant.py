"""Hardware/model-free exercise of the real pump, workers and state machine."""
import asyncio
from dataclasses import replace
import threading
import time

from .audio import AudioError, FRAME_BYTES
from .events import AssistantStateChanged


class MockCapture:
    def __init__(self, device):
        self.stop = threading.Event()
        self.sequence = 0

    def __enter__(self):
        return self

    def read_frame(self):
        if self.stop.wait(0.002):
            raise AudioError("Mock capture đã dừng.")
        self.sequence += 1
        # Five seconds of PCM between calls, replayed faster than real time.
        marker = {0: b"\x2a\0", 50: b"\x2b\0"}.get(self.sequence % 250, b"\0\0")
        return marker * (FRAME_BYTES // 2)

    def request_stop(self):
        self.stop.set()

    def __exit__(self, *_):
        self.request_stop()


class MockBackend:
    startup_seconds = 0.0

    def feed_command(self, frame, reset):
        return self.feed(frame, reset)

    def feed(self, frame, reset):
        text = {b"\x2a\0": "Maika ơi", b"\x2b\0": "bật đèn phòng khách"}.get(frame.pcm[:2])
        return ((text,) if text else ()), 0


class MockTurnVAD:
    """Scripted probabilities; exercises the actual production turn controller."""
    def reset(self):
        self.samples = self.offset = 0

    def process(self, pcm):
        from .turn import VadWindow
        self.samples += len(pcm) // 2
        result = []
        while self.samples - self.offset >= 512:
            result.append(VadWindow(self.offset, self.offset + 512,
                                    1.0 if self.offset < 16000 else 0.0))
            self.offset += 512
        return tuple(result)


class MockPlayer:
    def __init__(self, *_, echo_guard_seconds=0.1):
        self.process = None
        self.until = 0

    def play(self):
        self.process = True
        self.until = time.monotonic() + 0.040

    def suppressing(self):
        if time.monotonic() >= self.until - 0.020:
            self.process = None
        return time.monotonic() < self.until

    def close(self):
        self.process = None


async def run_mock(config, args, emit, status):
    from .assistant import session
    stop = asyncio.Event()
    cycles = 0
    def observe(event):
        nonlocal cycles
        if isinstance(event, AssistantStateChanged) and event.current == "sleeping":
            cycles += 1
            step = (" -> thu lượt" if getattr(args, "capture_only", False) else
                    "" if args.wake_only else " -> log lệnh")
            status(f"[MOCK ASSISTANT] Chu kỳ {cycles}: wake -> đáp{step} -> chờ wake.")
            if cycles == 3:
                stop.set()
    # A deterministic demo always includes three simulated acknowledgements.
    from argparse import Namespace
    mock_args = Namespace(**{**vars(args), "seconds": 5, "no_feedback": False})
    status("[MOCK ASSISTANT] PCM/engine/loa giả lập; không đọc model hoặc microphone.")
    runtime, _ = await session(replace(config, feedback_enabled=True), mock_args, emit, status,
                               stop=stop, observe=observe, source_factory=MockCapture,
                               backend_factory=MockBackend, player_factory=MockPlayer,
                               calibration_frames=0, vad_factory=MockTurnVAD)
    capture_only = getattr(args, "capture_only", False)
    passed = (runtime.trigger.events == runtime.cycles == runtime.acknowledgements == 3
              and runtime.commands == (0 if args.wake_only or capture_only else 3)
              and runtime.turns == (3 if capture_only else 0)
              and runtime.turn_timeouts == runtime.turn_aborts == 0
              and runtime.command_timeouts == runtime.command_aborts == 0
              and args.expect_events in (None, 3))
    status(f"[MOCK ASSISTANT] {'PASS' if passed else 'FAIL'}: {runtime.cycles}/3 chu kỳ.")
    return 0 if passed else 1
