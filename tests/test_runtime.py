"""Model-backed integration. No microphone, speaker or runtime downloads."""
import argparse
import asyncio
import importlib.util
import threading
import unittest

from smart_hub.assistant import session
from smart_hub.audio import AudioError, FRAME_BYTES, wav_frames
from smart_hub.config import Config, ROOT
from smart_hub.events import CommandRecognized, WakeEvent
from smart_hub.mock_assistant import MockPlayer
from smart_hub.stt_assets import verify_models


try:
    if importlib.util.find_spec("sherpa_onnx") is None:
        raise ImportError("Chưa có sherpa-onnx; cài requirements-stt.txt.")
    verify_models()
    MODEL_REASON = ""
except (ImportError, OSError, ValueError) as error:
    MODEL_REASON = str(error)


class ReplayCapture:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.opened = self.closed = 0

    def __enter__(self):
        self.opened += 1
        return self

    def read_frame(self):
        if not self.stop.wait(0.006):
            frame = next(self.frames, None)
            if frame is not None:
                return frame
            self.finished.set()
            self.stop.wait(20)
        raise AudioError("Replay đã dừng.")

    def request_stop(self):
        self.stop.set()

    def __exit__(self, *_):
        self.closed += 1


@unittest.skipIf(MODEL_REASON, MODEL_REASON)
class NativeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_model_three_wakes_and_negative_phrases_in_one_capture(self):
        frames = []
        for filename in ["0-1.wav", "1-1.wav", "0-1.wav", "0-3.wav", "0-4.wav", "0-5.wav"]:
            frames.extend(frame.ljust(FRAME_BYTES, b"\0")
                          for frame in wav_frames(ROOT / "tests/fixtures/stt" / filename))
            frames.extend([b"\0" * FRAME_BYTES] * 250)
        source = ReplayCapture(frames)
        stop = asyncio.Event()
        wakes = []
        args = argparse.Namespace(seconds=30, no_feedback=False, diagnostic=False, buffer_ms=500,
                                  wake_only=True, command_seconds=8)
        async def end_replay():
            async with asyncio.timeout(25):
                while not source.finished.is_set():
                    await asyncio.sleep(0.020)
                await asyncio.sleep(0.1)
                stop.set()
        timer = asyncio.create_task(end_replay())
        try:
            runtime, _ = await session(Config(), args, wakes.append, lambda _: None, stop=stop,
                                       source_factory=lambda _: source, player_factory=MockPlayer,
                                       calibration_frames=0)
            self.assertEqual(len(wakes), 3)
            self.assertEqual(runtime.cycles, 3)
            self.assertEqual(runtime.acknowledgements, 3)
            self.assertEqual(runtime.clipped, 0)
            self.assertEqual(runtime.pump.drop_counts["overflow"], 0)
            self.assertEqual(runtime.pump.drop_counts["stale"], 0)
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        self.assertEqual((source.opened, source.closed), (1, 1))
        self.assertFalse(runtime.pump.thread.is_alive())
        self.assertFalse(runtime.inference.thread.is_alive())
        self.assertFalse(runtime.playback.thread.is_alive())

    async def test_native_command_transcript_only_after_wake_and_ack(self):
        frames = []
        # The same utterance before wake is private background; after wake it is
        # the requested command transcript. Use existing Vietnamese fixtures.
        for filename in ("0-5.wav", "0-1.wav", "0-5.wav"):
            frames.extend(frame.ljust(FRAME_BYTES, b"\0")
                          for frame in wav_frames(ROOT / "tests/fixtures/stt" / filename))
            frames.extend([b"\0" * FRAME_BYTES] * 150)
        source = ReplayCapture(frames)
        stop = asyncio.Event()
        output, messages = [], []
        args = argparse.Namespace(seconds=20, no_feedback=False, diagnostic=False, buffer_ms=500,
                                  wake_only=False, command_seconds=8)
        async def end_replay():
            async with asyncio.timeout(15):
                while not source.finished.is_set():
                    await asyncio.sleep(0.020)
                await asyncio.sleep(0.1)
                stop.set()
        timer = asyncio.create_task(end_replay())
        try:
            runtime, _ = await session(Config(), args, output.append, messages.append, stop=stop,
                                       source_factory=lambda _: source, player_factory=MockPlayer,
                                       calibration_frames=0)
            self.assertEqual([type(event) for event in output], [WakeEvent, CommandRecognized])
            self.assertEqual(output[-1].text, "MAI ĐI CHƠI")
            self.assertEqual(output[-1].line(), "[COMMAND] MAI ĐI CHƠI")
            self.assertIsNotNone(output[-1].context.turn_id)
            self.assertEqual((runtime.cycles, runtime.commands, runtime.acknowledgements), (1, 1, 1))
            self.assertEqual((runtime.command_timeouts, runtime.command_aborts, runtime.clipped), (0, 0, 0))
            self.assertEqual(runtime.pump.drop_counts["overflow"], 0)
            self.assertEqual(runtime.pump.drop_counts["stale"], 0)
            self.assertEqual(sum("[LISTEN COMMAND]" in line for line in messages), 1)
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        self.assertEqual((source.opened, source.closed), (1, 1))
        self.assertFalse(runtime.pump.thread.is_alive())
        self.assertFalse(runtime.inference.thread.is_alive())
        self.assertFalse(runtime.playback.thread.is_alive())
