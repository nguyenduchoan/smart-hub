"""Production VAD/turn capture with existing synthetic Vietnamese audio."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smart_hub.assistant import session
from smart_hub.audio import FRAME_BYTES, wav_frames
from smart_hub.config import Config, ROOT
from smart_hub.events import UserTurnReady, WakeEvent
from smart_hub.mock_assistant import MockPlayer
from smart_hub.stt_assets import verify_models
from smart_hub.turn import TurnController
from smart_hub.turn_vad import SileroVAD
from test_runtime import ReplayCapture

try:
    if importlib.util.find_spec("onnxruntime") is None or importlib.util.find_spec("sherpa_onnx") is None:
        raise ImportError("Cần môi trường STT đã cài đặt.")
    verify_models()
    REASON = ""
except (ImportError, OSError, ValueError) as exc:
    REASON = str(exc)


@unittest.skipIf(REASON, REASON)
class SileroTurnTests(unittest.TestCase):
    def test_all_ten_vietnamese_fixtures_end_without_stt_or_forced_flush(self):
        vad = SileroVAD()
        provenance = json.loads((ROOT / "tests/fixtures/commands/provenance.json").read_text())
        for clip in provenance["clips"]:
            with self.subTest(clip=clip["file"]):
                path = ROOT / "tests/fixtures/commands" / clip["file"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), clip["sha256"])
                controller = TurnController()
                controller.begin(0)
                vad.reset()
                frames = [x.ljust(FRAME_BYTES, b"\0") for x in wav_frames(path)]
                frames += [b"\0" * FRAME_BYTES] * 50
                result = None
                for index, pcm in enumerate(frames):
                    result = controller.accept(pcm, vad.process(pcm), (index + 1) * .02)
                    if result:
                        break
                self.assertIsNotNone(result)
                self.assertEqual(result.reason, "complete")
                self.assertGreater(result.duration, .5)
                self.assertLessEqual(result.duration, 30.32)

    def test_real_silence_and_noise_never_create_turns(self):
        import numpy as np
        vad = SileroVAD()
        rng = np.random.default_rng(813)
        for label in ("silence", "noise"):
            with self.subTest(label=label):
                controller = TurnController()
                controller.begin(0)
                vad.reset()
                for index in range(410):
                    pcm = (b"\0" * 640 if label == "silence" else
                           rng.integers(-150, 151, size=320, dtype=np.int16).astype("<i2").tobytes())
                    outcome = controller.accept(pcm, vad.process(pcm), (index + 1) * .02)
                    if outcome:
                        break
                self.assertEqual((outcome.reason, outcome.pcm), ("no_speech", b""))

    def test_320_to_512_windows_preserve_every_sample_and_reset_residual(self):
        import numpy as np
        vad = SileroVAD()
        seen = []
        class Model:
            def run(self, _, inputs):
                seen.extend(inputs["x"][0])
                return np.zeros((1, 1)), inputs["h"], inputs["c"]
        vad.model = Model()
        pcm = np.arange(320 * 9, dtype="<i2")
        windows = []
        for start in range(0, len(pcm), 320):
            windows.extend(vad.process(pcm[start:start + 320].tobytes()))
        self.assertEqual([(x.start_sample, x.end_sample) for x in windows],
                         [(x, x + 512) for x in range(0, 2560, 512)])
        np.testing.assert_array_equal(seen, pcm[:2560] / 32768)
        self.assertEqual(bytes(vad.pending), pcm[2560:].tobytes())
        vad.reset()
        self.assertEqual((vad.offset, bytes(vad.pending)), (0, b""))
        self.assertFalse(vad.h.any() or vad.c.any())

    def test_missing_and_corrupt_assets_fail_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "silero.onnx"
            with self.assertRaises(FileNotFoundError):
                SileroVAD(path)
            path.write_bytes(b"not a model")
            with self.assertRaisesRegex(ValueError, "checksum"):
                SileroVAD(path)


@unittest.skipIf(REASON, REASON)
class NativeCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_wake_then_turn_in_one_capture_without_recording(self):
        frames = []
        # Background speech before wake cannot create a UserTurnReady event.
        for path in (ROOT / "tests/fixtures/commands/bat_den.wav",
                     ROOT / "tests/fixtures/stt/0-1.wav",
                     ROOT / "tests/fixtures/commands/nhiet_do.wav"):
            frames.extend(x.ljust(FRAME_BYTES, b"\0") for x in wav_frames(path))
            frames.extend([b"\0" * FRAME_BYTES] * 100)
        source = ReplayCapture(frames)
        stop = asyncio.Event()
        output = []
        def emit(event):
            output.append(event)
            if isinstance(event, UserTurnReady):
                stop.set()
        args = argparse.Namespace(seconds=15, no_feedback=False, diagnostic=False, buffer_ms=500,
                                  wake_only=False, capture_only=True, command_seconds=8)
        with patch("smart_hub.turn_recording.TurnRecorder", side_effect=AssertionError("default must not record")):
            runtime, _ = await session(Config(), args, emit, lambda _: None, stop=stop,
                                       source_factory=lambda _: source, player_factory=MockPlayer,
                                       calibration_frames=0)
        self.assertEqual([type(event) for event in output], [WakeEvent, UserTurnReady])
        self.assertEqual((runtime.turns, runtime.turn_aborts, runtime.commands), (1, 0, 0))
        self.assertEqual(runtime.clipped, 0)
        self.assertEqual(runtime.pump.drop_counts["overflow"] + runtime.pump.drop_counts["stale"], 0)
        self.assertEqual((source.opened, source.closed), (1, 1))
        self.assertFalse(runtime.pump.thread.is_alive() or runtime.inference.thread.is_alive()
                         or runtime.playback.thread.is_alive())
