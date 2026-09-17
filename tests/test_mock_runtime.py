import asyncio
from dataclasses import replace
import queue
import signal
import subprocess
import sys
import threading
import time
import unittest

from smart_hub.assistant import AssistantRuntime
from smart_hub.audio import AudioError, FRAME_BYTES
from smart_hub.audio_pump import AudioPump
from smart_hub.config import Config, ROOT
from smart_hub.events import (AudioFrame, AudioGap, AssistantStateChanged,
                              CommandRecognized, PlaybackStarted, PlaybackStopped, WorkContext)
from smart_hub.mock_assistant import MockBackend, MockPlayer
from smart_hub.state import AssistantState, StateMachine
from smart_hub.worker import SerialWorker


async def eventually(predicate, seconds=2):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.002)


class ControlledSource:
    def __init__(self):
        self.input = queue.Queue()
        self.opened = self.closed = self.reads = 0
        self.cleanup_started = threading.Event()
        self.cleanup_release = threading.Event()
        self.cleanup_release.set()

    def __enter__(self):
        self.opened += 1
        return self

    def read_frame(self):
        item = self.input.get(timeout=2)
        self.reads += 1
        if isinstance(item, Exception):
            raise item
        return item

    def request_stop(self):
        self.input.put(AudioError("test stopped"))

    def __exit__(self, *_):
        self.cleanup_started.set()
        self.cleanup_release.wait(2)
        self.closed += 1


class StateTests(unittest.TestCase):
    def test_legal_cycles_and_terminal_state(self):
        events = []
        machine = StateMachine(events.append)
        context = WorkContext("test", 2)
        for _ in range(3):
            machine.transition(AssistantState.ACKNOWLEDGING, context)
            machine.transition(AssistantState.SLEEPING, context)
        machine.transition(AssistantState.STOPPING, context)
        machine.transition(AssistantState.STOPPED, context)
        self.assertEqual(len(events), 8)
        self.assertTrue(all(event.context == context for event in events))
        with self.assertRaises(ValueError):
            machine.transition(AssistantState.SLEEPING, context)

    def test_illegal_transition_does_not_change_state(self):
        machine = StateMachine()
        with self.assertRaises(ValueError):
            machine.transition(AssistantState.STOPPED, WorkContext("test", 0))
        self.assertEqual(machine.state, AssistantState.SLEEPING)

    def test_pcm_is_not_exposed_in_event_representation(self):
        event = AudioFrame(1, 320, 10.0, b"private microphone audio")
        self.assertNotIn("private", repr(event))


class PumpTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_owner_bounded_overflow_gap_and_notifications(self):
        source = ControlledSource()
        async with AudioPump(source_factory=lambda _: source, buffer_ms=60, clock=lambda: 10) as pump:
            for _ in range(1000):
                source.input.put(b"\0" * FRAME_BYTES)
            await eventually(lambda: pump.dropped == 997)
            self.assertEqual(pump.max_pending, 3)
            # No consumer yet: a burst cannot schedule 1000 event-loop callbacks.
            self.assertLessEqual(pump.notifications, 2)
            gap = await pump.read()
            self.assertEqual(gap, AudioGap(0, 997 * 320, 997, ("overflow",)))
            frames = [await pump.read() for _ in range(3)]
            self.assertEqual([f.sequence for f in frames], [997, 998, 999])
            self.assertEqual([f.sample_offset for f in frames], [997*320, 998*320, 999*320])
            self.assertTrue(all(f.captured_at == 10 and f.sample_rate == 16000
                                and f.channels == 1 and f.format == "S16_LE" for f in frames))
        self.assertEqual((source.opened, source.closed), (1, 1))
        self.assertFalse(pump.thread.is_alive())

    async def test_stale_pcm_is_dropped_even_if_queue_is_not_full(self):
        source = ControlledSource()
        now = [10.0]
        async with AudioPump(source_factory=lambda _: source, clock=lambda: now[0]) as pump:
            source.input.put(b"\0" * FRAME_BYTES)
            await eventually(lambda: pump.max_pending == 1)
            now[0] = 11
            self.assertEqual(await pump.read(), AudioGap(0, 320, 1, ("stale",)))
            source.input.put(b"\0" * FRAME_BYTES)
            self.assertEqual((await pump.read()).sample_offset, 320)

    async def test_capture_failure_bypasses_full_pcm_and_slow_cleanup(self):
        source = ControlledSource()
        source.cleanup_release.clear()
        with self.assertRaisesRegex(AudioError, "microphone lost"):
            async with AudioPump(source_factory=lambda _: source, buffer_ms=20) as pump:
                try:
                    source.input.put(b"\0" * FRAME_BYTES)
                    source.input.put(AudioError("microphone lost"))
                    error = await asyncio.wait_for(asyncio.shield(pump.failed), 1)
                    self.assertIn("microphone lost", str(error))
                    self.assertEqual(source.closed, 0)
                    with self.assertRaisesRegex(AudioError, "microphone lost"):
                        await pump.read()
                    # Even when read's error is caught, exit must not pass.
                finally:
                    source.cleanup_release.set()

    async def test_cleanup_failure_is_reported_after_stop(self):
        class BrokenCleanup(ControlledSource):
            def __exit__(self, *_):
                super().__exit__()
                raise AudioError("capture cleanup failed")
        source = BrokenCleanup()
        with self.assertRaisesRegex(AudioError, "capture cleanup failed"):
            async with AudioPump(source_factory=lambda _: source) as pump:
                pass
        self.assertFalse(pump.thread.is_alive())

    async def test_startup_failure_closes_thread(self):
        def broken(_):
            raise AudioError("cannot open")
        pump = AudioPump(source_factory=broken)
        with self.assertRaisesRegex(AudioError, "cannot open"):
            async with pump:
                pass
        self.assertFalse(pump.thread.is_alive())

    async def test_playback_discard_advances_absolute_position(self):
        source = ControlledSource()
        async with AudioPump(source_factory=lambda _: source) as pump:
            for _ in range(3):
                source.input.put(b"\0" * FRAME_BYTES)
            await eventually(lambda: pump.max_pending == 3)
            pump.discard_pending()
            self.assertEqual(await pump.read(), AudioGap(0, 960, 3, ("playback",)))
            source.input.put(b"\0" * FRAME_BYTES)
            self.assertEqual((await pump.read()).sequence, 3)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_worker_has_no_backlog_and_does_not_block_loop(self):
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(2)
            return "late"
        async with SerialWorker("test-inference") as worker:
            task = asyncio.create_task(worker.call(slow))
            try:
                await eventually(entered.is_set)
                with self.assertRaisesRegex(RuntimeError, "đang xử lý"):
                    await worker.call(lambda: None)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(worker.busy)  # Cancelling Python does not stop native work.
            finally:
                release.set()
        self.assertFalse(worker.thread.is_alive())

    async def test_worker_shutdown_reports_timeout_instead_of_success(self):
        release = threading.Event()
        worker = await SerialWorker("test-timeout").__aenter__()
        task = asyncio.create_task(worker.call(release.wait, 2))
        try:
            await eventually(lambda: worker.active_future is not None)
            with self.assertRaisesRegex(RuntimeError, "không dừng"):
                await worker.close(timeout=0.01)
        finally:
            release.set()
            await worker.close()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(worker.thread.is_alive())


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def make_runtime(self, *, player=None, backend=None, command_seconds=None):
        source = ControlledSource()
        pump = await AudioPump(source_factory=lambda _: source).__aenter__()
        self.addAsyncCleanup(pump.__aexit__)
        inference = await SerialWorker("test-runtime-inference").__aenter__()
        self.addAsyncCleanup(inference.close)
        playback = await SerialWorker("test-runtime-playback").__aenter__()
        self.addAsyncCleanup(playback.close)
        if player:
            playback.finalizer = player.close
        events, wakes = [], []
        runtime = AssistantRuntime(Config(), pump, inference, backend or MockBackend(), playback, player,
                                   emit=wakes.append, observe=events.append, command_seconds=command_seconds)
        self.addAsyncCleanup(self.close_runtime_tasks, runtime)
        return runtime, source, events, wakes

    async def close_runtime_tasks(self, runtime):
        tasks = [task for task in (runtime.ack_task, runtime.command_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_three_cycles_and_late_control_cannot_complete_new_ack(self):
        runtime, _, events, wakes = await self.make_runtime(player=MockPlayer())
        stale = runtime.context
        for i in range(3):
            frame = AudioFrame(i * 250, i * 80000, time.monotonic() + 1, b"\0" * FRAME_BYTES)
            runtime.accept_result(runtime.context, runtime.pump.epoch, frame, (("Maika ơi",), 0))
            self.assertFalse(runtime.finish_ack(stale))
            await runtime.ack_task
            self.assertEqual(runtime.machine.state, AssistantState.SLEEPING)
        self.assertEqual(len(wakes), 3)
        self.assertEqual(runtime.cycles, 3)
        self.assertEqual(sum(isinstance(e, PlaybackStarted) for e in events), 3)
        self.assertEqual(sum(isinstance(e, PlaybackStopped) for e in events), 3)

    async def test_stale_generation_session_gap_and_duplicate_result_never_wake(self):
        runtime, _, _, wakes = await self.make_runtime()
        old = runtime.context
        runtime.invalidate()
        frame = AudioFrame(1, 320, time.monotonic(), b"\0" * FRAME_BYTES)
        result = (("Maika ơi",), 0)
        runtime.accept_result(old, 0, frame, result)
        runtime.accept_result(replace(runtime.context, session_id="other"), 0, frame, result)
        runtime.accept_result(runtime.context, -1, frame, result)
        self.assertEqual(wakes, [])
        runtime.accept_result(runtime.context, 0, frame, result)
        await runtime.ack_task
        runtime.after = -float("inf")
        runtime.accept_result(runtime.context, 0, frame, result)
        self.assertEqual(len(wakes), 1)

    async def test_feedback_and_echo_frames_are_not_sent_to_backend(self):
        class RecordingBackend(MockBackend):
            def __init__(self):
                self.seen = []
            def feed(self, frame, reset):
                self.seen.append((frame.sequence, reset))
                return (), 0
        backend = RecordingBackend()
        runtime, source, _, _ = await self.make_runtime(backend=backend, command_seconds=8)
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.run(stop))
        runtime.machine.transition(AssistantState.ACKNOWLEDGING, runtime.context)
        context = runtime.context
        source.input.put(b"\0" * FRAME_BYTES)
        await eventually(lambda: runtime.suppressed == 1)
        self.assertEqual(backend.seen, [])
        runtime.finish_ack(context)
        self.assertEqual(runtime.machine.state, AssistantState.LISTENING_COMMAND)
        # Simulate a frame already read during playback arriving after completion.
        runtime.pump._publish(AudioFrame(1, 320, runtime.after - 0.1, b"\0" * FRAME_BYTES))
        await eventually(lambda: runtime.suppressed == 2)
        await asyncio.sleep(0.025)
        source.input.put(b"\0" * FRAME_BYTES)
        await eventually(lambda: len(backend.seen) == 1)
        stop.set()
        await task
        self.assertTrue(backend.seen[0][1])

    async def test_slow_inference_loses_old_audio_and_late_result_is_rejected(self):
        entered, release = threading.Event(), threading.Event()
        class SlowBackend:
            def feed(self, frame, reset):
                entered.set()
                release.wait(2)
                return ("Maika ơi",), 0
        runtime, source, _, wakes = await self.make_runtime(backend=SlowBackend())
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.run(stop))
        try:
            source.input.put(b"\0" * FRAME_BYTES)
            await eventually(entered.is_set)
            for _ in range(30):
                source.input.put(b"\0" * FRAME_BYTES)
            await eventually(lambda: runtime.pump.dropped >= 5)
            # Stop signal is handled while native inference is still blocked.
            stop.set()
            self.assertTrue(await asyncio.wait_for(task, 0.5))
            self.assertEqual(wakes, [])
        finally:
            release.set()
            stop.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_gap_resets_backend_before_processing_fresh_audio(self):
        entered, release = threading.Event(), threading.Event()
        class Backend:
            def __init__(self):
                self.calls = []
            def feed(self, frame, reset):
                self.calls.append((frame.sequence, reset))
                if len(self.calls) == 1:
                    entered.set()
                    release.wait(2)
                    return ("Maika ơi",), 0
                return (), 0
        backend = Backend()
        runtime, source, _, wakes = await self.make_runtime(backend=backend)
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.run(stop))
        try:
            source.input.put(b"\0" * FRAME_BYTES)
            await eventually(entered.is_set)
            for _ in range(30):
                source.input.put(b"\0" * FRAME_BYTES)
            await eventually(lambda: runtime.pump.dropped == 5)
            release.set()
            await eventually(lambda: len(backend.calls) >= 2)
            self.assertEqual(backend.calls[:2], [(0, True), (6, True)])
            self.assertEqual(wakes, [])
            self.assertEqual(runtime.stale_results, 1)
        finally:
            stop.set()
            release.set()
            await task

    async def test_playback_failure_terminates_without_waiting_for_pcm(self):
        class BrokenPlayer(MockPlayer):
            def play(self):
                raise RuntimeError("speaker failed")
        runtime, source, events, _ = await self.make_runtime(player=BrokenPlayer())
        task = asyncio.create_task(runtime.run(asyncio.Event()))
        source.input.put(b"\x2a\0" * 320)
        with self.assertRaisesRegex(RuntimeError, "speaker failed"):
            await asyncio.wait_for(task, 1)
        self.assertTrue(any(isinstance(e, AssistantStateChanged) and e.current == "failed" for e in events))

    async def test_cancel_at_worker_completion_is_not_swallowed(self):
        runtime, source, _, _ = await self.make_runtime()
        class CompletingWorker:
            async def call(self, *_):
                asyncio.get_running_loop().call_soon(consumer.cancel)
                return (), 0
        runtime.inference = CompletingWorker()
        consumer = asyncio.create_task(runtime._consume())
        source.input.put(b"\0" * FRAME_BYTES)
        with self.assertRaises(asyncio.CancelledError):
            async with asyncio.timeout(0.2):
                await consumer

    async def test_command_is_logged_once_after_ack_and_next_wake_works(self):
        runtime, _, events, output = await self.make_runtime(player=MockPlayer(), command_seconds=8)
        messages = []
        runtime.status = messages.append
        def result(sequence, texts):
            frame = AudioFrame(sequence, sequence * 320, time.monotonic() + 1, b"\0" * FRAME_BYTES)
            runtime.accept_result(runtime.context, runtime.pump.epoch, frame, (texts, 0))
        result(1, ("chuyện riêng trước khi gọi",))
        self.assertEqual(output, [])
        turn_ids = []
        for sequence in (250, 500):
            result(sequence, ("Maika ơi", "không được dùng đoạn cùng lượt wake làm lệnh"))
            self.assertEqual(runtime.machine.state, AssistantState.ACKNOWLEDGING)
            self.assertFalse(any("LISTEN COMMAND" in line for line in messages))
            await runtime.ack_task
            self.assertEqual(runtime.machine.state, AssistantState.LISTENING_COMMAND)
            self.assertTrue(runtime.reset_needed)
            turn_ids.append(runtime.context.turn_id)
            self.assertIn("[LISTEN COMMAND]", messages[-1])
            result(sequence + 1, ("  ",))
            self.assertEqual(runtime.machine.state, AssistantState.LISTENING_COMMAND)
            result(sequence + 2, ("  bật đèn\nphòng khách  ", "câu thứ hai không log"))
            self.assertEqual(runtime.machine.state, AssistantState.SLEEPING)
            # A duplicate final result cannot print or re-wake in the new generation.
            result(sequence + 2, ("Maika ơi",))
            messages.clear()
        commands = [event for event in output if isinstance(event, CommandRecognized)]
        self.assertEqual([event.line() for event in commands], ["[COMMAND] bật đèn phòng khách"] * 2)
        self.assertEqual([event.context.turn_id for event in commands], turn_ids)
        self.assertEqual(len(set(turn_ids)), 2)
        self.assertEqual(runtime.trigger.events, 2)
        self.assertEqual((runtime.cycles, runtime.commands, runtime.acknowledgements), (2, 2, 2))
        self.assertEqual(sum(isinstance(event, CommandRecognized) for event in events), 2)
        self.assertIsNone(runtime.command_task)

    async def test_wake_transcript_is_opt_in_and_does_not_relax_matching(self):
        runtime, _, _, output = await self.make_runtime()
        logs = []
        runtime.status = logs.append
        frame = AudioFrame(1, 320, time.monotonic() + 1, b"\0" * FRAME_BYTES)
        runtime.accept_result(runtime.context, runtime.pump.epoch, frame, (("nội dung riêng",), 0))
        self.assertEqual(logs, [])
        runtime.show_wake_text = True
        runtime.accept_result(runtime.context, runtime.pump.epoch, replace(frame, sequence=2),
                              (("  MAI CẢ\nƠI  ",), 0))
        self.assertEqual(logs, ["[WAKE TEXT] 'MAI CẢ ƠI'"])
        self.assertEqual(output, [])

    async def test_command_timeout_without_pcm_returns_to_wake_and_rejects_late_text(self):
        runtime, _, _, output = await self.make_runtime(command_seconds=0.04)
        messages = []
        runtime.status = messages.append
        frame = AudioFrame(250, 80000, time.monotonic() + 1, b"\0" * FRAME_BYTES)
        runtime.accept_result(runtime.context, 0, frame, (("Maika ơi",), 0))
        await runtime.ack_task
        context = runtime.context
        await eventually(lambda: runtime.cycles == 1)
        runtime.accept_result(context, 0, replace(frame, sequence=251), (("bật đèn",), 0))
        self.assertEqual(runtime.machine.state, AssistantState.SLEEPING)
        self.assertEqual((runtime.commands, runtime.command_timeouts), (0, 1))
        self.assertEqual(len(output), 1)
        self.assertEqual(sum("[COMMAND TIMEOUT]" in line for line in messages), 1)
        self.assertIsNone(runtime.command_task)

    async def test_command_deadline_rejects_text_even_before_timeout_callback_runs(self):
        runtime, _, _, output = await self.make_runtime(command_seconds=8)
        frame = AudioFrame(250, 80000, time.monotonic() + 1, b"\0" * FRAME_BYTES)
        runtime.accept_result(runtime.context, 0, frame, (("Maika ơi",), 0))
        await runtime.ack_task
        runtime.command_deadline = time.monotonic() - 1
        runtime.accept_result(runtime.context, 0, replace(frame, sequence=251), (("bật đèn",), 0))
        self.assertEqual((runtime.commands, runtime.command_timeouts, runtime.cycles), (0, 1, 1))
        self.assertEqual(len(output), 1)

    async def test_old_context_echo_and_discontinuous_command_results_are_rejected(self):
        runtime, _, _, output = await self.make_runtime(command_seconds=8)
        old = runtime.context
        frame = AudioFrame(250, 80000, time.monotonic() + 1, b"\0" * FRAME_BYTES)
        runtime.accept_result(runtime.context, 0, frame, (("Maika ơi",), 0))
        await runtime.ack_task
        command_frame = replace(frame, sequence=251)
        result = (("bật đèn",), 0)
        runtime.accept_result(old, 0, command_frame, result)
        runtime.accept_result(replace(runtime.context, session_id="other"), 0, command_frame, result)
        runtime.accept_result(runtime.context, -1, command_frame, result)
        runtime.accept_result(runtime.context, 0, replace(command_frame, captured_at=runtime.after), result)
        self.assertEqual(len(output), 1)
        runtime.accept_result(runtime.context, 0, command_frame, result)
        self.assertEqual(runtime.commands, 1)

    async def test_audio_loss_aborts_command_but_playback_boundary_does_not(self):
        for reason in ("playback", "overflow", "stale"):
            with self.subTest(reason=reason):
                runtime, source, _, output = await self.make_runtime(command_seconds=8)
                frame = AudioFrame(0, 0, time.monotonic() + 1, b"\0" * FRAME_BYTES)
                runtime.accept_result(runtime.context, 0, frame, (("Maika ơi",), 0))
                await runtime.ack_task
                source.input.put(b"\0" * FRAME_BYTES)
                await eventually(lambda: runtime.pump.max_pending == 1)
                runtime.pump.discard_pending(reason)
                stop = asyncio.Event()
                task = asyncio.create_task(runtime.run(stop))
                try:
                    await eventually(lambda: runtime.pump.gap is None)
                    self.assertEqual(runtime.command_aborts, 0 if reason == "playback" else 1)
                    self.assertEqual(runtime.machine.state, AssistantState.LISTENING_COMMAND
                                     if reason == "playback" else AssistantState.SLEEPING)
                    self.assertEqual(len(output), 1)
                finally:
                    stop.set()
                    await task

    async def test_clipped_command_is_discarded_instead_of_logging_partial_text(self):
        runtime, _, _, output = await self.make_runtime(command_seconds=8)
        frame = AudioFrame(250, 80000, time.monotonic() + 1, b"\0" * FRAME_BYTES)
        runtime.accept_result(runtime.context, 0, frame, (("Maika ơi",), 0))
        await runtime.ack_task
        runtime.accept_result(runtime.context, 0, replace(frame, sequence=251), (("bật",), 1))
        self.assertEqual((runtime.commands, runtime.command_aborts, runtime.clipped), (0, 1, 1))
        self.assertEqual(runtime.machine.state, AssistantState.SLEEPING)
        self.assertEqual(len(output), 1)

    async def test_clipping_warns_while_listening_once_and_fresh_wake_still_works(self):
        runtime, _, _, output = await self.make_runtime()
        messages = []
        runtime.status = messages.append
        for sequence in range(1, 4):
            frame = AudioFrame(sequence, sequence * 320, time.monotonic(), b"\0" * FRAME_BYTES)
            runtime.accept_result(runtime.context, runtime.pump.epoch, frame, ((), 1))
            self.assertEqual(output, [])
            self.assertEqual(runtime.machine.state, AssistantState.SLEEPING)
            self.assertEqual(len(messages), 1)
            self.assertIn("[AUDIO WARNING]", messages[0])
        self.assertEqual(runtime.clipped, 3)
        frame = AudioFrame(250, 80000, time.monotonic(), b"\0" * FRAME_BYTES)
        runtime.accept_result(runtime.context, runtime.pump.epoch, frame, (("Maika ơi",), 0))
        await runtime.ack_task
        self.assertEqual((runtime.trigger.events, runtime.cycles), (1, 1))
        self.assertEqual(len(output), 1)
        self.assertEqual(len(messages), 1)

    async def test_command_uses_its_own_feed_and_accepts_first_frame_after_ready(self):
        class SeparateBackend(MockBackend):
            def __init__(self):
                self.calls = []
            def feed(self, frame, reset):
                self.calls.append(("wake", frame.sequence))
                return ("Maika ơi",), 0
            def feed_command(self, frame, reset):
                self.calls.append(("command", frame.sequence))
                return ("BẬT QUẠT",), 0
        backend = SeparateBackend()
        runtime, source, _, output = await self.make_runtime(backend=backend, command_seconds=8)
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.run(stop))
        try:
            source.input.put(b"\0" * FRAME_BYTES)
            await eventually(lambda: runtime.machine.state == AssistantState.LISTENING_COMMAND)
            # No 20 ms sleep here: READY must really be ready for the first syllable.
            source.input.put(b"\x64\0" * 320)
            await eventually(lambda: runtime.commands == 1)
            self.assertEqual(backend.calls, [("wake", 0), ("command", 1)])
            self.assertEqual(output[-1].line(), "[COMMAND] BẬT QUẠT")
            self.assertEqual(runtime.cycles, 1)
        finally:
            stop.set()
            await task

    async def test_stop_during_command_inference_cancels_deadline_and_late_output(self):
        entered, release = threading.Event(), threading.Event()
        class SlowCommandBackend(MockBackend):
            def feed(self, frame, reset):
                entered.set()
                release.wait(2)
                return ("bật đèn",), 0
        runtime, source, _, output = await self.make_runtime(backend=SlowCommandBackend(), command_seconds=8)
        frame = AudioFrame(0, 0, time.monotonic() + 1, b"\0" * FRAME_BYTES)
        runtime.accept_result(runtime.context, 0, frame, (("Maika ơi",), 0))
        await runtime.ack_task
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.run(stop))
        try:
            await asyncio.sleep(0.025)
            source.input.put(b"\0" * FRAME_BYTES)
            await eventually(entered.is_set)
            stop.set()
            async with asyncio.timeout(0.5):
                self.assertTrue(await task)
            self.assertEqual(len(output), 1)
            self.assertEqual((runtime.commands, runtime.command_timeouts), (0, 0))
            self.assertIsNone(runtime.command_task)
        finally:
            release.set()
            stop.set()
            await asyncio.gather(task, return_exceptions=True)


class RuntimeCLITests(unittest.TestCase):
    def test_signals_during_startup_and_listening_never_pass_incomplete_test(self):
        program = '''
import asyncio, os, signal, sys, time
sys.path.insert(0, "src")
import smart_hub.assistant as assistant
from smart_hub.cli import main
from smart_hub.mock_assistant import MockBackend, MockCapture, MockPlayer
original = assistant.session
class SlowBackend(MockBackend):
    def __init__(self):
        time.sleep(0.1)
async def fake_session(*args, **kwargs):
    asyncio.get_running_loop().call_later(0.02, os.kill, os.getpid(), getattr(signal, sys.argv[1]))
    return await original(*args, **kwargs, source_factory=MockCapture,
                          backend_factory=SlowBackend if sys.argv[2] == "startup" else MockBackend,
                          player_factory=MockPlayer, calibration_frames=0)
assistant.session = fake_session
sys.exit(main(["assistant", "--seconds", "20", "--expect-events", "0"]))
'''
        for kind, stage in [("SIGINT", "listen"), ("SIGTERM", "listen"), ("SIGTERM", "startup")]:
            with self.subTest(signal=kind, stage=stage):
                result = subprocess.run([sys.executable, "-c", program, kind, stage], cwd=ROOT,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 130, result.stderr)
                self.assertIn("INCOMPLETE", result.stderr)
                self.assertNotIn("[TEST] PASS", result.stderr)
                self.assertNotIn("[FAIL]", result.stderr)

    def test_mock_cli_three_cycles_without_audio_or_models(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"),
                                 "assistant", "--mock"], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("[WAKE]"), 3)
        self.assertEqual(result.stdout.count("[COMMAND] bật đèn phòng khách"), 3)
        self.assertEqual(result.stderr.count("[LISTEN COMMAND]"), 3)
        self.assertIn("[MOCK ASSISTANT] PASS: 3/3", result.stderr)

    def test_wake_only_keeps_previous_mock_behavior(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"),
                                 "assistant", "--mock", "--wake-only"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("[WAKE]"), 3)
        self.assertNotIn("[COMMAND]", result.stdout)
        self.assertNotIn("[LISTEN COMMAND]", result.stderr)

    def test_sensitive_wake_and_short_reply_options_keep_the_complete_mock_cycle(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"),
                                 "assistant", "--mock", "--wake-profile", "sensitive",
                                 "--reply-guard", "0.1", "--show-wake-text", "--diagnostic"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("wake-profile=sensitive; reply-guard=0.1s", result.stderr)
        self.assertEqual(result.stdout.count("[WAKE]"), 3)
        self.assertEqual(result.stderr.count("[WAKE TEXT] 'Maika ơi'"), 3)
        # The mock also speaks before the first wake. Opt-in diagnostics must
        # expose that non-matching transcript so missed wakes can be diagnosed.
        self.assertIn("[WAKE TEXT] 'bật đèn phòng khách'", result.stderr)
        self.assertEqual(result.stdout.count("[COMMAND]"), 3)

    def test_invalid_reply_guard_is_rejected_before_startup(self):
        for value in ("-0.1", "nan", "inf", "2.1"):
            with self.subTest(value=value):
                result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"),
                                         "assistant", "--mock", "--reply-guard", value],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("[ENGINE]", result.stderr)

    def test_invalid_command_window_is_rejected_before_startup(self):
        for value in ("0", "-1", "nan", "inf", "31"):
            with self.subTest(value=value):
                result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"),
                                         "assistant", "--mock", "--command-seconds", value],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("[ENGINE]", result.stderr)

    def test_mock_wrong_expected_count_fails(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"),
                                 "assistant", "--mock", "--expect-events", "2"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("[MOCK ASSISTANT] FAIL", result.stderr)
