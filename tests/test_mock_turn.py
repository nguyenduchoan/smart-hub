"""Turn policy and runtime contracts; no numpy, models, microphone or speaker."""
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from smart_hub.assistant import AssistantRuntime
from smart_hub.config import Config, ROOT, load_config
from smart_hub.events import AudioFrame, AudioGap, UserTurnReady, UserTurnAborted, WorkContext
from smart_hub.mock_assistant import MockBackend
from smart_hub.state import AssistantState
from smart_hub.turn import TurnController, TurnSettings, VadWindow
from smart_hub.turn_recording import TurnRecorder
from test_mock_runtime import ControlledSource, eventually
from smart_hub.audio_pump import AudioPump
from smart_hub.worker import SerialWorker

PCM = b"\x64\0" * 320


class ScriptedVAD:
    def __init__(self, probability):
        self.probability = probability
        self.reset()

    def reset(self):
        self.received = self.offset = 0

    def process(self, pcm):
        self.received += len(pcm) // 2
        windows = []
        while self.received - self.offset >= 512:
            windows.append(VadWindow(self.offset, self.offset + 512, self.probability(self.offset / 16000)))
            self.offset += 512
        return tuple(windows)


def replay(controller, vad, seconds, *, now_scale=1):
    result = None
    for index in range(round(seconds / .020)):
        result = controller.accept(PCM, vad.process(PCM), (index + 1) * .020 * now_scale)
        if result:
            break
    return result


class TurnPolicyTests(unittest.TestCase):
    def test_complete_keeps_preroll_and_trailing_silence(self):
        controller = TurnController()
        controller.begin(0)
        outcome = replay(controller, ScriptedVAD(lambda t: .9 if .5 <= t < 1.5 else .1), 3)
        self.assertEqual(outcome.reason, "complete")
        # First positive window starts at .512; returned PCM starts .32 s earlier.
        self.assertAlmostEqual(outcome.duration, 2.208 - (.512 - .32))
        self.assertEqual(outcome.pcm, b"\x64\0" * round(outcome.duration * 16000))
        self.assertFalse(controller.open)
        self.assertEqual(controller.buffer.pcm, b"")
        self.assertIsNone(controller.accept(PCM, (), 4))

    def test_mid_sentence_pause_stays_in_one_turn(self):
        controller = TurnController()
        controller.begin(0)
        outcome = replay(controller, ScriptedVAD(lambda t: .9 if t < .6 or 1.0 <= t < 1.7 else .1), 3)
        self.assertEqual(outcome.reason, "complete")
        self.assertGreater(outcome.duration, 2.3)

    def test_short_co_khong_dung_have_explicit_minimum_speech_outcomes(self):
        for text in ("có", "không", "dừng"):
            for minimum, expected in ((.25, "no_speech"), (.05, "complete")):
                with self.subTest(text=text, minimum=minimum):
                    controller = TurnController(TurnSettings(min_speech_seconds=minimum, wait_seconds=1))
                    controller.begin(0)
                    outcome = replay(controller, ScriptedVAD(lambda t: .9 if t < .096 else .1), 2)
                    self.assertEqual(outcome.reason, expected)

    def test_silence_and_short_noise_never_create_a_turn(self):
        controller = TurnController(TurnSettings(wait_seconds=2))
        controller.begin(0)
        outcome = replay(controller, ScriptedVAD(lambda t: .9 if .3 <= t < .4 else .1), 2.5)
        self.assertEqual((outcome.reason, outcome.pcm), ("no_speech", b""))

    def test_waiting_buffer_is_bounded_even_when_clock_does_not_advance(self):
        controller = TurnController(TurnSettings(wait_seconds=30))
        controller.begin(0)
        vad = ScriptedVAD(lambda _: .1)
        for _ in range(1490):
            self.assertIsNone(controller.accept(PCM, vad.process(PCM), 0))
            self.assertLessEqual(len(controller.buffer.pcm), 2 * (math.ceil((.32 + .25) * 16000) + 1024))

    def test_max_duration_and_missing_audio_deadline_return_no_pcm(self):
        controller = TurnController(TurnSettings(max_seconds=2))
        controller.begin(0)
        outcome = replay(controller, ScriptedVAD(lambda _: .9), 3, now_scale=.1)
        self.assertEqual((outcome.reason, outcome.pcm), ("max_duration", b""))
        controller.begin(0)
        replay(controller, ScriptedVAD(lambda _: .9), .5)
        self.assertEqual(controller.poll(2.01).reason, "max_duration")
        controller.begin(0)
        self.assertEqual(controller.poll(8).reason, "no_speech")

    def test_clipping_and_cancellation_erase_the_turn(self):
        controller = TurnController()
        controller.begin(0)
        replay(controller, ScriptedVAD(lambda _: .9), .5)
        outcome = controller.accept(b"\xff\x7f" * 320, (), .6)
        self.assertEqual((outcome.reason, outcome.pcm), ("clipping", b""))
        self.assertEqual(controller.buffer.pcm, b"")
        controller.begin(1)
        controller.cancel()
        self.assertIsNone(controller.poll(100))

    def test_invalid_or_discontinuous_vad_is_rejected(self):
        for window in (VadWindow(512, 1024, .9), VadWindow(0, 512, float("nan")),
                       VadWindow(0, 640, .9), VadWindow(0, 512, 1.01)):
            controller = TurnController()
            controller.begin(0)
            controller.accept(PCM, (), .02)
            with self.subTest(window=window), self.assertRaises(ValueError):
                controller.accept(PCM, (window,), .04)

    def test_turn_config_validated_when_loading_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"turn": {"min_speech_seconds": .1, "max_seconds": 15}}))
            self.assertEqual(load_config(path).turn.min_speech_seconds, .1)
            for invalid in (True, None, {"max_seconds": 31}, {"wait_seconds": "8"},
                            {"vad_threshold": float("nan")}, {"preroll_seconds": -1},
                            {"max_seconds": 1, "silence_seconds": 1}, {"unknown": 5}):
                path.write_text(json.dumps({"turn": invalid}))
                with self.subTest(invalid=invalid), self.assertRaises((ValueError, TypeError)):
                    load_config(path)


class TurnRecordingTests(unittest.TestCase):
    def test_debug_wav_is_private_exact_pcm_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = TurnRecorder(Path(folder) / "recordings")
            event = UserTurnReady(WorkContext("test", 1, "turn"), PCM * 5)
            first, second = recorder.write(event), recorder.write(event)
            self.assertNotEqual(first, second)
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            self.assertEqual(recorder.directory.stat().st_mode & 0o777, 0o700)
            with wave.open(str(first), "rb") as audio:
                self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getsampwidth()), (16000, 1, 2))
                self.assertEqual(audio.readframes(audio.getnframes()), event.pcm)
            self.assertNotIn("pcm=", repr(event))

    def test_failed_recording_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = TurnRecorder(folder)
            with patch("smart_hub.turn_recording.wave.open", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    recorder.write(UserTurnReady(WorkContext("x", 0), PCM))
            self.assertEqual(list(recorder.directory.iterdir()), [])


class CaptureRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.source = ControlledSource()
        self.pump = await AudioPump(source_factory=lambda _: self.source).__aenter__()
        self.worker = await SerialWorker("test-turn").__aenter__()
        self.output, self.observed, self.messages = [], [], []
        self.runtime = AssistantRuntime(Config(), self.pump, self.worker, MockBackend(), None, None,
                                        emit=self.output.append, observe=self.observed.append,
                                        status=self.messages.append, turn_vad=ScriptedVAD(lambda _: .9))
        self.runtime.machine.transition(AssistantState.ACKNOWLEDGING, self.runtime.context)
        self.runtime.finish_ack(self.runtime.context)

    async def asyncTearDown(self):
        if self.runtime.turn_task:
            self.runtime.turn_task.cancel()
            await asyncio.gather(self.runtime.turn_task, return_exceptions=True)
        if self.runtime.turn_io_task:
            await asyncio.gather(self.runtime.turn_io_task, return_exceptions=True)
        await self.pump.__aexit__()
        await self.worker.close()

    def deliver(self, index, probability=.9, pcm=PCM):
        frame = AudioFrame(index, index * 320, self.runtime.after + .1 + index * .02, pcm,
                           continuity_id=self.pump.epoch)
        self.runtime.turn_vad.probability = lambda _: probability
        result = self.runtime._feed_turn(frame, self.runtime.reset_needed)
        self.runtime.reset_needed = False
        self.runtime.accept_result(self.runtime.context, self.pump.epoch, frame, result)

    async def test_capture_only_returns_one_turn_without_command_transcript_or_wake(self):
        for index in range(100):
            if self.runtime.machine.state != AssistantState.LISTENING_TURN:
                break
            self.deliver(index, .9 if index < 40 else .1)
        self.assertEqual([type(event) for event in self.output], [UserTurnReady])
        self.assertEqual((self.runtime.turns, self.runtime.cycles, self.runtime.commands), (1, 1, 0))
        self.assertEqual(self.runtime.trigger.events, 0)
        self.assertIsNotNone(self.output[0].context.turn_id)
        self.assertEqual(self.runtime.machine.state, AssistantState.SLEEPING)

    async def test_no_speech_timer_operates_without_audio_frames(self):
        self.runtime.turn_controller.deadline = time.monotonic() + .03
        self.runtime.turn_deadline_changed.set()
        await eventually(lambda: self.runtime.turn_timeouts == 1)
        self.assertEqual(self.output, [])
        self.assertEqual(self.runtime.cycles, 1)

    async def test_max_timer_is_rescheduled_after_speech_start(self):
        self.runtime.turn_controller.settings = TurnSettings(max_seconds=1.1)
        for index in range(20):
            self.deliver(index)
        # Existing no-speech deadline is 8 seconds; speech changed it to 1.1.
        await eventually(lambda: self.runtime.turn_aborts == 1, seconds=2)
        self.assertEqual(self.output, [])
        self.assertEqual([event.reason for event in self.observed if isinstance(event, UserTurnAborted)],
                         ["max_duration"])

    async def test_clipping_produces_no_ready_or_recording(self):
        self.deliver(0, pcm=b"\0\x80" * 320)
        self.assertEqual((self.runtime.clipped, self.runtime.turn_aborts), (1, 1))
        self.assertEqual(self.output, [])

    async def test_offset_gap_aborts_partial_turn(self):
        self.deliver(0)
        self.deliver(2)
        self.assertEqual(self.runtime.turn_aborts, 1)
        self.assertEqual(self.output, [])

    async def test_overflow_during_vad_does_not_publish_a_partial_turn(self):
        self.deliver(0)
        context = self.runtime.context
        self.pump.epoch += 1
        self.runtime.accept_result(context, 0, AudioFrame(1, 320, time.monotonic() + 1, PCM), ())
        self.assertEqual(self.runtime.turn_aborts, 1)
        self.assertEqual(self.output, [])

    async def test_audio_gap_from_pump_aborts_and_stop_cleans_up(self):
        stop = asyncio.Event()
        with self.pump.lock:
            self.pump.gap = AudioGap(0, 320, 1, ("overflow",))
        task = asyncio.create_task(self.runtime.run(stop))
        try:
            await eventually(lambda: self.runtime.turn_aborts == 1)
            self.assertEqual(self.output, [])
        finally:
            stop.set()
            await task
        self.assertEqual(self.runtime.turn_controller.buffer.pcm, b"")

    async def test_playback_gap_and_echo_never_seed_new_turn(self):
        before = self.runtime.context
        stop = asyncio.Event()
        with self.pump.lock:
            self.pump.gap = AudioGap(0, 320, 1, ("playback",))
        task = asyncio.create_task(self.runtime.run(stop))
        try:
            await asyncio.sleep(.02)
            self.assertEqual(self.runtime.context, before)
            self.assertEqual(self.runtime.turn_aborts, 0)
            self.assertEqual(self.runtime.turn_controller.buffer.pcm, b"")
            # The first post-READY frame is accepted, including tentative speech.
            self.source.input.put(PCM)
            await eventually(lambda: len(self.runtime.turn_controller.buffer.pcm) == 640)
        finally:
            stop.set()
            await task
        self.assertEqual(self.output, [])

    async def test_runtime_stop_discards_an_unfinished_turn_and_timeout(self):
        self.deliver(0)
        stop = asyncio.Event()
        task = asyncio.create_task(self.runtime.run(stop))
        await asyncio.sleep(.01)
        stop.set()
        await task
        self.assertEqual(self.output, [])
        self.assertFalse(self.runtime.turn_controller.open)
        self.assertEqual(self.runtime.turn_controller.buffer.pcm, b"")
        self.assertTrue(self.runtime.turn_task is None or self.runtime.turn_task.done())

    async def test_stale_generation_does_not_abort_a_new_turn(self):
        context = self.runtime.context
        self.runtime.invalidate()
        self.runtime.accept_result(context, self.pump.epoch,
                                   AudioFrame(0, 0, time.monotonic() + 1, PCM), ())
        self.assertEqual(self.runtime.turn_aborts, 0)
        self.assertEqual(self.output, [])

    async def test_late_result_after_stop_never_produces_turn(self):
        self.runtime.stop.set()
        self.deliver(0)
        self.assertEqual((self.runtime.turns, self.runtime.turn_aborts), (0, 0))
        self.assertEqual(self.output, [])

    async def test_saving_debug_audio_uses_worker_and_returns_to_wake(self):
        with tempfile.TemporaryDirectory() as folder:
            self.runtime.recorder = TurnRecorder(folder)
            for index in range(100):
                if self.runtime.machine.state != AssistantState.LISTENING_TURN:
                    break
                self.deliver(index, .9 if index < 40 else .1)
            await self.runtime.turn_io_task
            self.assertEqual(len(list(Path(folder).rglob("*.wav"))), 1)
            self.assertEqual(self.runtime.machine.state, AssistantState.SLEEPING)


class CaptureCLITests(unittest.TestCase):
    def test_mock_capture_runs_three_full_cycles_without_numpy_or_models(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"),
                                 "assistant", "--capture-only", "--mock"],
                                capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("[TURN]"), 3)
        self.assertEqual(result.stdout.count("[WAKE]"), 3)
        self.assertNotIn("[COMMAND]", result.stdout)

    def test_incompatible_modes_fail_before_audio_opens(self):
        for flags in (["--capture-only", "--wake-only"], ["--debug-recordings"],
                      ["--capture-only", "--mock", "--debug-recordings"]):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"), "assistant", *flags],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("[ENGINE]", result.stderr)
