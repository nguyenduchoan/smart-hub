import contextlib
import importlib.util
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from smart_hub.audio import AudioError, wav_frames
from smart_hub.capture_pump import CapturePump
from smart_hub.cli import main
from smart_hub.config import Config, ROOT
from smart_hub.stt_assets import FILES, MODEL_DIR, verify_bytes
from smart_hub.stt_wake import STTSession


def arguments(**overrides):
    return SimpleNamespace(**dict(alias=[], diagnostic=False, show_text=False, **overrides))


class STTSessionTests(unittest.TestCase):
    def test_clipped_frame_is_rejected_even_without_a_vad_segment(self):
        backend = Mock()
        events = []
        session = STTSession(backend, Config(), arguments(), events.append, lambda _: None)
        session.feed(b"\0\x80" * 320)
        self.assertEqual(session.clipped, 1)
        backend.feed.assert_not_called()
        backend.transcribe.assert_not_called()
        backend.reset.assert_called_once()
        self.assertEqual(events, [])

    def test_interrupted_stt_retains_counts_and_closes_even_if_count_matches(self):
        backend = Mock(startup_seconds=0.1)
        backend.feed.return_value = [np.full(16000, 0.05, dtype=np.float32)]
        backend.transcribe.return_value = "MAI CA ƠI"
        source = MagicMock()
        source.__enter__.return_value = source
        source.read_frame.side_effect = [b"\x64\0" * 320] * 51 + [KeyboardInterrupt()]
        with patch("smart_hub.local_stt.LocalSTT", return_value=backend), \
                patch("smart_hub.stt_wake.CapturePump", return_value=source), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            code = main(["listen-stt", "--seconds", "30", "--expect-events", "1", "--no-feedback"])
        self.assertEqual(code, 130)
        self.assertEqual(output.getvalue().count("[WAKE]"), 1)
        self.assertIn("[STOP STT] 1 wake event", errors.getvalue())
        self.assertIn("INCOMPLETE", errors.getvalue())
        self.assertNotIn("[TEST] PASS", errors.getvalue())
        source.__exit__.assert_called_once()

    def test_legacy_listen_also_retains_stats_on_interrupt(self):
        detector = SimpleNamespace(events=2, max_score=0.8, rejected_clipping=0)
        with patch("smart_hub.engine.create_engine"), \
                patch("smart_hub.detector.create_detector", return_value=detector), \
                patch("smart_hub.cli.wav_frames", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            code = main(["listen", "--wav", "unused.wav", "--expect-events", "0", "--diagnostic"])
        self.assertEqual(code, 130)
        self.assertIn("[STOP] 2 wake event", errors.getvalue())
        self.assertIn("INCOMPLETE", errors.getvalue())

    def test_reply_and_echo_are_drained_before_next_call(self):
        backend = Mock()
        backend.feed.return_value = [np.full(16000, 0.05, dtype=np.float32)]
        backend.transcribe.return_value = "MAI CA ƠI"
        player = Mock()
        player.suppressing.return_value = False
        events = []
        session = STTSession(backend, Config(), arguments(), events.append, lambda _: None, player)
        frame = b"\0" * 640
        session.feed(frame)
        self.assertEqual(len(events), 1)
        player.suppressing.return_value = True
        for _ in range(150):
            session.feed(frame)
        self.assertEqual(backend.feed.call_count, 1)
        self.assertEqual(backend.transcribe.call_count, 1)
        player.suppressing.return_value = False
        session.feed(frame)
        self.assertEqual(len(events), 2)
        self.assertEqual(player.play.call_count, 2)

    def test_clipping_and_failed_capture_do_not_emit(self):
        backend = Mock()
        backend.transcribe.return_value = "Maika ơi"
        events = []
        session = STTSession(backend, Config(), arguments(), events.append, lambda _: None)
        session.process([np.ones(16000, dtype=np.float32)])
        backend.transcribe.assert_not_called()
        self.assertEqual(session.clipped, 1)
        session.health_check = Mock(side_effect=AudioError("capture failed"))
        with self.assertRaisesRegex(AudioError, "capture failed"):
            session.process([np.full(16000, 0.05, dtype=np.float32)])
        self.assertEqual(events, [])

    def test_transcripts_are_not_logged_by_default(self):
        backend = Mock()
        backend.transcribe.return_value = "nội dung cuộc trò chuyện riêng"
        logs = []
        session = STTSession(backend, Config(), arguments(), lambda _: None, logs.append)
        session.process([np.zeros(16000, dtype=np.float32)])
        self.assertEqual(logs, [])


class PumpTests(unittest.TestCase):
    def test_capture_error_is_visible_before_slow_child_cleanup_finishes(self):
        produce = threading.Event()
        cleaning_up = threading.Event()
        finish_cleanup = threading.Event()

        class Source:
            def __init__(self, _):
                pass
            def __enter__(self):
                return self
            def read_frame(self):
                produce.wait(timeout=2)
                return b"\0" * 640
            def __exit__(self, *_):
                cleaning_up.set()
                finish_cleanup.wait(timeout=2)

        pump = CapturePump(source_factory=Source, capacity=1)
        pump.__enter__()
        try:
            produce.set()
            self.assertTrue(cleaning_up.wait(timeout=2))
            # A decoder can finish here, while arecord cleanup is still waiting.
            with self.assertRaisesRegex(AudioError, "buffer"):
                pump.check_health()
        finally:
            finish_cleanup.set()
            pump.__exit__(RuntimeError)
        self.assertFalse(pump.thread.is_alive())

    def test_single_capture_owner_and_clean_shutdown(self):
        class Source:
            opened = 0
            closed = 0
            def __init__(self, _):
                pass
            def __enter__(self):
                self.__class__.opened += 1
                return self
            def read_frame(self):
                time.sleep(0.01)
                return b"\0" * 640
            def __exit__(self, *_):
                self.__class__.closed += 1
        with CapturePump(source_factory=Source) as pump:
            for _ in range(10):
                self.assertEqual(len(pump.read_frame()), 640)
        self.assertEqual((Source.opened, Source.closed), (1, 1))
        self.assertFalse(pump.thread.is_alive())

    def test_overflow_fails_and_closes_instead_of_unbounded_buffering(self):
        release = threading.Event()
        closed = threading.Event()
        class Source:
            def __init__(self, _):
                pass
            def __enter__(self):
                return self
            def read_frame(self):
                release.wait(timeout=2)
                return b"\0" * 640
            def __exit__(self, *_):
                closed.set()
        pump = CapturePump(source_factory=Source, capacity=1)
        with self.assertRaisesRegex(AudioError, "buffer"):
            with pump:
                release.set()
                self.assertTrue(closed.wait(timeout=2))
                pump.check_health()
        self.assertLessEqual(pump.queue.qsize(), 1)
        self.assertFalse(pump.thread.is_alive())

    def test_startup_error_is_propagated(self):
        def unavailable(_):
            raise AudioError("no microphone")
        with self.assertRaisesRegex(AudioError, "no microphone"):
            with CapturePump(source_factory=unavailable):
                self.fail("Unavailable microphone must not become ready")


HAVE_STT = importlib.util.find_spec("sherpa_onnx") is not None and all((MODEL_DIR / f).is_file() for f in FILES)


class STTWindowTests(unittest.TestCase):
    def test_command_gain_is_bounded_and_never_changes_wake_or_raw_pcm(self):
        from smart_hub.local_stt import LocalSTT
        model = LocalSTT.__new__(LocalSTT)
        samples = np.array([-0.01, 0, 0.01], dtype=np.float32)
        original = samples.copy()
        model.command_mode = False
        model.wake_max_gain = 1.0
        self.assertIs(model._recognition_audio(samples, 0.5), samples)
        model.command_mode = True
        boosted = model._recognition_audio(samples, 0.5)
        self.assertGreater(float(np.max(np.abs(boosted))), float(np.max(np.abs(samples))))
        self.assertLessEqual(float(np.max(np.abs(boosted))), 8 * float(np.max(np.abs(samples))))
        np.testing.assert_array_equal(samples, original)
        loud = np.array([-0.98, 0, 0.98], dtype=np.float32)
        np.testing.assert_array_equal(model._recognition_audio(loud, 0.5), loud)
        np.testing.assert_array_equal(model._recognition_audio(np.zeros(512), 0.9), np.zeros(512))
        model.vad = Mock()
        model.pending, model.history, model.total_samples = bytearray(), bytearray(), 0
        model.vad.empty.return_value = True
        pcm = b"\x64\0" * 320
        model.feed(pcm)
        model.feed(pcm)
        self.assertEqual(model.history, pcm * 2)
        self.assertLess(float(np.max(np.abs(model.vad.accept_waveform.call_args.args[0]))), 1)

    def test_longer_command_preroll_keeps_a_soft_first_word(self):
        from smart_hub.local_stt import LocalSTT
        pcm = np.arange(20000, dtype="<i2")
        for preroll, expected_start in ((5120, 4880), (10240, 0)):
            with self.subTest(preroll=preroll):
                model = LocalSTT.__new__(LocalSTT)
                model.command_mode = False
                model.preroll_samples = preroll
                model.total_samples = len(pcm)
                model.history = bytearray(pcm.tobytes())
                model.vad = Mock()
                model.vad.empty.side_effect = [False, True]
                # A late VAD onset lies after the first, softer word.
                model.vad.front = SimpleNamespace(start=10000, samples=np.zeros(3200))
                segments = model._segments()
                self.assertEqual(len(segments), 1)
                np.testing.assert_array_equal(segments[0], pcm[expected_start:14800].astype(np.float32) / 32768)
                model.vad.pop.assert_called_once()


@unittest.skipUnless(HAVE_STT, "STT tùy chọn: cài requirements-stt.txt và download_stt_models.py để chạy model thật")
class RealSTTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from smart_hub.local_stt import LocalSTT
        cls.engine = LocalSTT()

    def setUp(self):
        self.engine.set_command_mode(False)
        self.engine.reset()

    def test_command_profile_reuses_asr_and_restores_original_wake_vad(self):
        wake_vad, recognizer = self.engine.vad, self.engine.recognizer
        self.engine.feed(b"\x64\0" * 320)
        self.engine.set_command_mode(True)
        command_vad = self.engine.vad
        self.assertIsNot(command_vad, wake_vad)
        self.assertIs(self.engine.recognizer, recognizer)
        self.assertEqual(self.engine.preroll_samples, 10240)
        self.assertEqual((self.engine.total_samples, len(self.engine.history), len(self.engine.pending)), (0, 0, 0))
        texts = []
        for frame in wav_frames(ROOT / "tests/fixtures/stt/0-5.wav"):
            texts.extend(self.engine.transcribe(samples) for samples in self.engine.feed(frame))
        for _ in range(50):
            texts.extend(self.engine.transcribe(samples) for samples in self.engine.feed(b"\0" * 640))
        self.assertEqual(texts, ["MAI ĐI CHƠI"])
        self.engine.set_command_mode(False)
        self.assertIs(self.engine.vad, wake_vad)
        self.assertIs(self.engine.recognizer, recognizer)
        self.assertEqual(self.engine.preroll_samples, 5120)
        self.assertEqual((self.engine.total_samples, len(self.engine.history), len(self.engine.pending)), (0, 0, 0))
        self.engine.set_command_mode(True)
        self.assertIs(self.engine.vad, command_vad)

    def test_assistant_prepares_command_vad_before_any_microphone_frame(self):
        from smart_hub.assistant import STTWakeBackend
        from smart_hub.events import AudioFrame
        backend = STTWakeBackend(wake_profile="sensitive")
        command_vad, wake_vad = backend.model.command_vad, backend.model.wake_vad
        self.assertIsNotNone(command_vad)
        self.assertFalse(backend.model.command_mode)
        frame = AudioFrame(0, 0, 1.0, b"\0" * 640)
        with patch("sherpa_onnx.VoiceActivityDetector", side_effect=AssertionError("VAD loaded after READY")):
            self.assertEqual(backend.feed_command(frame, True), ((), 0))
            self.assertIs(backend.model.vad, command_vad)
            self.assertEqual(backend.feed(frame, True), ((), 0))
            self.assertIs(backend.model.vad, wake_vad)

    def test_sensitive_wake_on_quiet_faster_synthetic_calls_and_negative_phrases(self):
        from smart_hub.local_stt import LocalSTT
        from smart_hub.stt_keyword import KeywordTrigger
        model = LocalSTT(wake_profile="sensitive")
        root = ROOT / "tests/fixtures/stt"
        cases = json.loads((root / "provenance.json").read_text())["clips"]
        # Speed/pitch alteration is a stress test, not a recording of a child.
        for case in cases:
            original = np.frombuffer(b"".join(wav_frames(root / case["file"])), dtype="<i2").astype(np.float32)
            for speed, gain in ((1.0, 1.0), (1.25, 0.03)):
                with self.subTest(file=case["file"], speed=speed, gain=gain):
                    samples = np.interp(np.arange(0, len(original), speed), np.arange(len(original)), original) * gain
                    pcm = np.rint(samples).astype("<i2").tobytes() + b"\0" * 16000
                    model.reset()
                    events, segment_id = [], 0
                    trigger = KeywordTrigger("Maika ơi", events.append)
                    for start in range(0, len(pcm), 640):
                        for audio in model.feed(pcm[start:start + 640]):
                            segment_id += 1
                            trigger.accept(model.transcribe(audio), segment_id, (start + 640) / 32000)
                    self.assertEqual(len(events), int(case["expected_wake"]))

    def test_short_vietnamese_commands_keep_bat_and_tat_distinct(self):
        root = ROOT / "tests/fixtures/commands"
        cases = json.loads((root / "provenance.json").read_text())["clips"]
        self.engine.set_command_mode(True)
        for case in cases:
            if case["file"] not in ("bat_quat.wav", "tat_quat.wav"):
                continue
            for gain in (1.0, 0.1):
                with self.subTest(phrase=case["text"], gain=gain):
                    self.engine.reset()
                    texts = []
                    for frame in wav_frames(root / case["file"]):
                        pcm = np.rint(np.frombuffer(frame, dtype="<i2").astype(np.float32) * gain).astype("<i2").tobytes()
                        texts.extend(self.engine.transcribe(samples) for samples in self.engine.feed(pcm))
                    # No offline flush: the live path must detect the end itself.
                    self.assertEqual(texts, [case["expected_text"]])

    def test_diverse_command_corpus_does_not_regress_below_measured_accuracy(self):
        import wave
        root = ROOT / "tests/fixtures/commands"
        cases = json.loads((root / "provenance.json").read_text())["clips"]
        self.assertEqual(len(cases), 10)
        self.assertEqual(len({case["voice"] for case in cases}), 2)
        self.engine.set_command_mode(True)
        # Accuracy floors, not a claim that every transcript is correct.
        # Remaining errors and the complete comparison are documented.
        for condition, required in (("normal", 8), ("quiet", 9), ("noise", 8)):
            correct = 0
            failures = []
            for case in cases:
                with wave.open(str(root / case["file"])) as wav:
                    clean = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
                samples = clean.copy()
                if condition == "quiet":
                    samples *= 0.1
                elif condition == "noise":
                    active = clean[np.abs(clean) > 0.01]
                    rms = float(np.sqrt(np.mean(active * active)))
                    samples += np.random.default_rng(2026).normal(
                        0, rms / (10 ** (15 / 20)), len(samples)).astype(np.float32)
                pcm = np.rint(samples * 32767).astype("<i2").tobytes()
                self.engine.reset()
                texts = []
                for start in range(0, len(pcm), 640):
                    texts.extend(self.engine.transcribe(segment)
                                 for segment in self.engine.feed(pcm[start:start + 640]))
                if texts == [case["expected_text"]]:
                    correct += 1
                else:
                    failures.append((case["expected_text"], texts))
            with self.subTest(condition=condition, exact_matches=correct):
                self.assertGreaterEqual(correct, required, failures)

    def test_command_gain_does_not_turn_silence_or_steady_noise_into_speech(self):
        self.engine.set_command_mode(True)
        for amplitude in (0, 0.005, 0.03):
            with self.subTest(noise_rms=amplitude):
                self.engine.reset()
                pcm = np.rint(np.random.default_rng(2026).normal(0, amplitude, 8 * 16000) * 32767).astype("<i2").tobytes()
                for start in range(0, len(pcm), 640):
                    self.assertEqual(self.engine.feed(pcm[start:start + 640]), [])

    def test_silence_and_frame_remainders_are_bounded(self):
        for _ in range(1000):
            self.assertEqual(self.engine.feed(b"\0" * 640), [])
            self.assertLess(len(self.engine.pending), 1024)
            self.assertLessEqual(len(self.engine.history), 8 * 16000 * 2)
        self.assertEqual(self.engine.flush(), [])

    def test_real_models_on_synthetic_positive_and_negative_fixtures(self):
        root = ROOT / "tests/fixtures/stt"
        cases = json.loads((root / "provenance.json").read_text())["clips"]
        for case in cases:
            with self.subTest(file=case["file"], voice=case["voice"]):
                self.engine.reset()
                events = []
                session = STTSession(self.engine, Config(), arguments(), events.append, lambda _: None)
                for frame in wav_frames(root / case["file"]):
                    session.feed(frame)
                session.process(self.engine.flush())
                self.assertEqual(len(events), int(case["expected_wake"]))
                self.assertEqual(session.clipped, 0)

    def test_offline_cli_has_one_event_and_never_opens_audio_devices(self):
        with patch("smart_hub.stt_wake.CapturePump") as mic, \
                patch("smart_hub.stt_wake.VoiceFeedback") as speaker, \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()):
            code = main(["listen-stt", "--wav", str(ROOT / "tests/fixtures/stt/0-1.wav"), "--expect-events", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue().count("[WAKE]"), 1)
        mic.assert_not_called()
        speaker.assert_not_called()

    def test_ten_synthetic_calls_in_one_session_after_long_idle(self):
        events = []
        session = STTSession(self.engine, Config(), arguments(), events.append, lambda _: None)
        phrase = list(wav_frames(ROOT / "tests/fixtures/stt/0-1.wav"))
        for index in range(10):
            for frame in phrase:
                session.feed(frame)
            for _ in range(3250 if index == 4 else 200):
                session.feed(b"\0" * 640)
            self.assertEqual(len(events), index + 1)
        session.process(self.engine.flush())
        self.assertEqual(len(events), 10)

    def test_wrong_model_checksum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify_bytes("encoder.int8.onnx", b"not a model")
