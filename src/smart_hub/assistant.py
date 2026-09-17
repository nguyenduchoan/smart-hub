"""Local wake -> fixed reply -> one command transcript -> sleeping lifecycle."""
import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace
from functools import partial
import math
import signal
import time
import uuid

from .audio import AlsaCapture, AudioError, RATE, pcm_stats
from .audio_pump import AudioPump
from .events import (AudioGap, CommandRecognized, PlaybackStarted, PlaybackStopped, RuntimeErrorEvent,
                     UserTurnAborted, UserTurnReady, WakeDetected, WorkContext)
from .feedback import VoiceFeedback
from .state import AssistantState, StateMachine
from .stt_keyword import KeywordTrigger
from .worker import SerialWorker
from .turn import TurnController, TurnOutcome

LISTENING_STATES = (AssistantState.SLEEPING, AssistantState.LISTENING_COMMAND, AssistantState.LISTENING_TURN)


class STTWakeBackend:
    """All model operations belong to the inference worker, including startup."""
    def __init__(self, *, wake_profile="standard", command_enabled=True):
        from .local_stt import LocalSTT
        started = time.monotonic()
        self.model = LocalSTT(wake_profile=wake_profile)
        if command_enabled:
            # Prepare VAD before opening capture, not on the first command frame.
            self.model.set_command_mode(True)
            self.model.set_command_mode(False)
        self.startup_seconds = time.monotonic() - started

    def feed(self, frame, reset):
        self.model.set_command_mode(False)
        return self._feed(frame, reset)

    def feed_command(self, frame, reset):
        self.model.set_command_mode(True)
        return self._feed(frame, reset)

    def _feed(self, frame, reset):
        import numpy as np
        if reset:
            self.model.reset()
        if pcm_stats(frame.pcm)["clipped_percent"] > 1:
            self.model.reset()
            return (), 1
        texts, clipped = [], 0
        for samples in self.model.feed(frame.pcm):
            if float(np.mean(np.abs(samples) >= 32767 / 32768)) > 0.01:
                clipped += 1
                continue
            texts.append(self.model.transcribe(samples))
        return tuple(texts), clipped


class AssistantRuntime:
    def __init__(self, config, pump, inference, backend, playback, player, *,
                 emit, observe=lambda event: None, status=lambda text: None,
                 command_seconds=8.0, show_wake_text=False, turn_vad=None, recorder=None,
                 clock=time.monotonic):
        if command_seconds is not None and (not math.isfinite(command_seconds) or not 0 < command_seconds <= 30):
            raise ValueError("Cửa sổ nghe lệnh cần lớn hơn 0 và tối đa 30 giây.")
        self.pump, self.inference, self.backend = pump, inference, backend
        self.playback, self.player, self.emit, self.observe = playback, player, emit, observe
        self.status, self.command_seconds = status, command_seconds
        self.show_wake_text = show_wake_text
        self.turn_vad, self.recorder = turn_vad, recorder
        self.turn_controller = TurnController(config.turn) if turn_vad is not None else None
        self.turns = self.turn_timeouts = self.turn_aborts = 0
        self.turn_task = self.turn_io_task = None
        self.turn_deadline_changed = asyncio.Event()
        self.turn_next_sample = None
        self.clock = clock
        self.context = WorkContext(uuid.uuid4().hex, 0)
        self.machine = StateMachine(observe)
        self.trigger = KeywordTrigger(config.wake_word, self._wake,
                                      cooldown_seconds=config.cooldown_seconds)
        self.reset_needed = True
        self.accepting = True
        self.after = -float("inf")
        self.last_sequence = -1
        self.segments = self.clipped = self.stale_results = self.suppressed = 0
        self.cycles = self.acknowledgements = 0
        self.commands = self.command_timeouts = self.command_aborts = 0
        self.command_task = self.command_deadline = None
        self.ack_task = None
        self.playing_context = None
        self.failed = asyncio.get_running_loop().create_future()
        self.stop = asyncio.Event()

    def invalidate(self):
        self.context = replace(self.context, generation_id=self.context.generation_id + 1)
        self.reset_needed = True

    def _wake(self, event):
        self.invalidate()
        self.machine.transition(AssistantState.ACKNOWLEDGING, self.context)
        self.pump.discard_pending()
        self.observe(WakeDetected(self.context, event))
        self.emit(event)
        self.ack_task = asyncio.create_task(self._acknowledge(self.context), name="assistant-ack")
        self.ack_task.add_done_callback(self._ack_done)

    def _ack_done(self, task):
        if not task.cancelled() and task.exception() is not None and not self.failed.done():
            self.failed.set_result(task.exception())

    def finish_ack(self, context):
        if context != self.context or self.machine.state != AssistantState.ACKNOWLEDGING:
            return False
        # Discard queued echo, including a frame straddling the guard boundary.
        self.after = self.clock()
        self.pump.discard_pending()
        self.invalidate()
        if self.turn_controller is not None:
            if not self.accepting or self.stop.is_set():
                return False
            self.context = replace(self.context, turn_id=uuid.uuid4().hex)
            self.turn_controller.begin(self.clock())
            self.turn_next_sample = None
            self.turn_deadline_changed.clear()
            self.machine.transition(AssistantState.LISTENING_TURN, self.context)
            self.turn_task = asyncio.create_task(self._turn_timeout(self.context), name="assistant-turn-timeout")
            self.turn_task.add_done_callback(self._ack_done)
            self.status("[LISTEN TURN] Hãy nói một câu; Maika tự chốt sau khoảng im lặng.")
        elif self.command_seconds is not None:
            if not self.accepting or self.stop.is_set():
                return False
            self.context = replace(self.context, turn_id=uuid.uuid4().hex)
            self.command_deadline = self.clock() + self.command_seconds
            self.command_task = asyncio.create_task(self._command_timeout(self.context),
                                                     name="assistant-command-timeout")
            self.command_task.add_done_callback(self._ack_done)
            self.machine.transition(AssistantState.LISTENING_COMMAND, self.context)
            self.status(f"[LISTEN COMMAND] Nói một câu trong {self.command_seconds:g} giây.")
        else:
            self.cycles += 1
            self.machine.transition(AssistantState.SLEEPING, self.context)
        return True

    async def _turn_timeout(self, context):
        while context == self.context and self.turn_controller.open:
            delay = max(0, self.turn_controller.deadline - self.clock())
            try:
                async with asyncio.timeout(delay):
                    await self.turn_deadline_changed.wait()
                self.turn_deadline_changed.clear()
            except TimeoutError:
                outcome = self.turn_controller.poll(self.clock())
                if outcome is not None:
                    self.finish_turn(context, outcome)
                    return

    def _turn_sleep(self):
        self.context = replace(self.context, turn_id=None)
        self.cycles += 1
        self.machine.transition(AssistantState.SLEEPING, self.context)
        self.status(f"[READY ASSISTANT] Đang nghe ‘{self.trigger.phrase}’; Ctrl+C để dừng.")

    async def _save_turn(self, event, context):
        async with asyncio.timeout(5):
            path = await self.inference.call(self.recorder.write, event)
        if context == self.context and self.accepting and not self.stop.is_set():
            self.status(f"[RECORDING] {path}")
            self.after = self.clock() + .020
            self.pump.discard_pending()
            self._turn_sleep()

    def finish_turn(self, context, outcome):
        if (context != self.context or self.machine.state != AssistantState.LISTENING_TURN
                or not self.accepting or self.stop.is_set()):
            return False
        self.turn_controller.cancel()
        if self.turn_task is not None and self.turn_task is not asyncio.current_task():
            self.turn_task.cancel()
        self.turn_task = None
        self.after = self.clock() + .020
        self.pump.discard_pending()
        self.invalidate()
        if outcome.reason == "complete":
            event = UserTurnReady(context, outcome.pcm)
            self.turns += 1
            self.observe(event)
            self.emit(event)
            if self.recorder is not None:
                self.machine.transition(AssistantState.SAVING_TURN, self.context)
                self.turn_io_task = asyncio.create_task(self._save_turn(event, self.context),
                                                        name="assistant-turn-recording")
                self.turn_io_task.add_done_callback(self._ack_done)
                return True
        else:
            self.observe(UserTurnAborted(context, outcome.reason))
            if outcome.reason == "no_speech":
                self.turn_timeouts += 1
                self.status("[TURN TIMEOUT] Chưa nghe được câu nói; hãy gọi lại Maika.")
            else:
                self.turn_aborts += 1
                self.status(f"[TURN INCOMPLETE] {outcome.reason}; hãy gọi lại và nói lại câu.")
        self._turn_sleep()
        return True

    def _feed_turn(self, frame, reset):
        if reset:
            self.turn_vad.reset()
        return self.turn_vad.process(frame.pcm)

    async def _command_timeout(self, context):
        await asyncio.sleep(self.command_seconds)
        self.finish_command(context, reason="timeout")

    def finish_command(self, context, *, text=None, reason=None):
        if (context != self.context or self.machine.state != AssistantState.LISTENING_COMMAND
                or not self.accepting or self.stop.is_set()):
            return False
        if self.command_task is not None and self.command_task is not asyncio.current_task():
            self.command_task.cancel()
        self.command_task = self.command_deadline = None
        if text is not None:
            event = CommandRecognized(context, " ".join(text.split()))
            self.commands += 1
            self.observe(event)
            self.emit(event)
        elif reason == "timeout":
            self.command_timeouts += 1
            self.status("[COMMAND TIMEOUT] Chưa nhận được câu nói hoàn chỉnh; hãy gọi lại wake word.")
        else:
            self.command_aborts += 1
            self.status(f"[COMMAND DROPPED] {reason}; hãy gọi lại wake word.")
        # Do not interpret queued command audio as a fresh wake result.
        self.after = self.clock() + 0.020
        self.invalidate()
        self.context = replace(self.context, turn_id=None)
        self.cycles += 1
        self.machine.transition(AssistantState.SLEEPING, self.context)
        self.status(f"[READY ASSISTANT] Đang nghe ‘{self.trigger.phrase}’; Ctrl+C để dừng.")
        return True

    def _poll_player(self):
        return self.player.suppressing(), self.player.process is not None

    async def _acknowledge(self, context):
        if self.player is not None:
            async with asyncio.timeout(5):
                await self.playback.call(self.player.play)
            self.playing_context = context
            self.observe(PlaybackStarted(context, self.clock()))
            while True:
                async with asyncio.timeout(5):
                    suppressing, running = await self.playback.call(self._poll_player)
                if not running and self.playing_context is not None:
                    self.observe(PlaybackStopped(context, self.clock()))
                    self.playing_context = None
                if not suppressing:
                    break
                await asyncio.sleep(0.020)
            self.acknowledgements += 1
        # Complete the straddling-frame guard BEFORE announcing readiness.
        await asyncio.sleep(0.020)
        self.finish_ack(context)

    def accept_result(self, context, epoch, frame, result):
        self.pump.check_health()
        if (not self.accepting or self.stop.is_set() or context != self.context or epoch != self.pump.epoch
                or self.machine.state not in LISTENING_STATES
                or frame.captured_at <= self.after or frame.sequence <= self.last_sequence):
            self.stale_results += 1
            self.reset_needed = True
            if context == self.context and self.machine.state == AssistantState.LISTENING_TURN:
                self.finish_turn(context, TurnOutcome("audio_gap"))
            return
        self.last_sequence = frame.sequence
        if self.machine.state == AssistantState.LISTENING_TURN:
            if self.turn_next_sample is not None and frame.sample_offset != self.turn_next_sample:
                self.finish_turn(context, TurnOutcome("audio_gap"))
                return
            self.turn_next_sample = frame.end_sample
            old_deadline = self.turn_controller.deadline
            outcome = self.turn_controller.accept(frame.pcm, result, self.clock())
            if outcome is not None:
                if outcome.reason == "clipping":
                    self.clipped += 1
                    self.status("[AUDIO WARNING] Mic vỡ tiếng; đã hủy lượt nói.")
                self.finish_turn(context, outcome)
            elif self.turn_controller.deadline != old_deadline:
                self.turn_deadline_changed.set()
            return
        texts, clipped = result
        if clipped and self.clipped == 0:
            self.status("[AUDIO WARNING] Mic vỡ tiếng (clipping); STT phải bỏ audio. "
                        "Kiểm tra gain mic bằng check-mic khi đang nói. "
                        "Cảnh báo này chỉ hiện một lần trong phiên.")
        self.clipped += clipped
        if self.machine.state == AssistantState.LISTENING_COMMAND:
            if self.clock() >= self.command_deadline:
                self.finish_command(context, reason="timeout")
            elif clipped:
                self.finish_command(context, reason="Audio clipping, đã bỏ lượt nghe này")
            else:
                for text in texts:
                    if text.strip():
                        self.finish_command(context, text=text)
                        break  # Exactly one final transcript per wake, never dispatch a device action.
            return
        for text in texts:
            self.segments += 1
            if self.show_wake_text:
                self.status(f"[WAKE TEXT] {' '.join(text.split())!r}")
            if self.trigger.accept(text, self.segments, frame.end_sample / RATE):
                break  # One final wake result starts one acknowledgement generation.

    async def _consume(self):
        while (not self.stop.is_set() and self.machine.state in
               (*LISTENING_STATES, AssistantState.ACKNOWLEDGING, AssistantState.SAVING_TURN)):
            item = await self.pump.read()
            if isinstance(item, AudioGap):
                self.observe(item)
                # Playback owns its generation until the echo guard ends. Gaps
                # there discard audio but must not invalidate the completion.
                if self.machine.state == AssistantState.SLEEPING:
                    self.invalidate()
                elif self.machine.state == AssistantState.LISTENING_TURN:
                    if any(reason != "playback" for reason in item.reasons):
                        self.finish_turn(self.context, TurnOutcome("audio_gap"))
                    # Intentional playback discard was completed before begin().
                    # Do not reset an in-progress VAD history for that old gap.
                elif self.machine.state == AssistantState.LISTENING_COMMAND and any(
                        reason != "playback" for reason in item.reasons):
                    self.finish_command(self.context, reason="Mất audio, đã bỏ lượt nghe này")
                else:
                    self.reset_needed = True
                continue
            if (not self.accepting or self.machine.state not in LISTENING_STATES
                    or item.captured_at <= self.after):
                self.suppressed += 1
                continue
            context, epoch = self.context, item.continuity_id
            reset, self.reset_needed = self.reset_needed, False
            try:
                # Python 3.11's wait_for can swallow cancellation when its inner
                # task finishes concurrently. Keep the deadline in this task.
                async with asyncio.timeout(5):
                    feed = (self._feed_turn if self.machine.state == AssistantState.LISTENING_TURN else
                            self.backend.feed_command if self.machine.state == AssistantState.LISTENING_COMMAND
                            else self.backend.feed)
                    result = await self.inference.call(feed, item, reset)
            except TimeoutError:
                raise RuntimeError("STT không trả kết quả trong 5 giây; phiên đã dừng.") from None
            self.accept_result(context, epoch, item, result)

    async def run(self, stop, seconds=None):
        self.stop = stop
        consumer = asyncio.create_task(self._consume(), name="assistant-consume")
        stopper = asyncio.create_task(stop.wait())
        timer = asyncio.create_task(asyncio.sleep(seconds)) if seconds is not None else None
        controls = {consumer, stopper, self.pump.failed, self.failed}
        try:
            done, _ = await asyncio.wait(controls | ({timer} if timer else set()),
                                         return_when=asyncio.FIRST_COMPLETED)
            self.accepting = False
            # Natural test completion finishes the current reply. Signals stop
            # promptly, and capture/playback failures always override a timer.
            if timer in done and stopper not in done and self.ack_task is not None:
                done, _ = await asyncio.wait(controls | {self.ack_task},
                                             return_when=asyncio.FIRST_COMPLETED)
            self.pump.check_health()
            if self.failed.done():
                raise self.failed.result()
            if consumer.done():
                consumer.result()
            return stop.is_set()
        except Exception as exc:
            self.observe(RuntimeErrorEvent(self.context, str(exc)))
            self.machine.transition(AssistantState.FAILED, self.context)
            raise
        finally:
            self.accepting = False
            self.invalidate()
            if self.turn_controller is not None:
                self.turn_controller.cancel()
            self.machine.transition(AssistantState.STOPPING, self.context)
            tasks = [consumer, stopper, *([timer] if timer else []),
                     *([self.ack_task] if self.ack_task else []),
                     *([self.command_task] if self.command_task else []),
                     *([self.turn_task] if self.turn_task else []),
                     *([self.turn_io_task] if self.turn_io_task else [])]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.command_task = self.command_deadline = None


async def session(config, args, emit, status, *, stop, observe=lambda event: None,
                  source_factory=AlsaCapture, backend_factory=None,
                  player_factory=VoiceFeedback, calibration_frames=50, initialized=None,
                  vad_factory=None):
    runtime = None
    playback = None
    capture_only = getattr(args, "capture_only", False)
    debug_recordings = getattr(args, "debug_recordings", False)
    if capture_only and args.wake_only:
        raise ValueError("capture-only không kết hợp wake-only.")
    if debug_recordings and not capture_only:
        raise ValueError("debug-recordings chỉ dùng cùng capture-only.")
    wake_profile = getattr(args, "wake_profile", "standard")
    reply_guard = getattr(args, "reply_guard", 0.1)
    if not math.isfinite(reply_guard) or not 0 <= reply_guard <= 2:
        raise ValueError("Khoảng chờ tiếng vọng cần từ 0 đến 2 giây.")
    if backend_factory is None:
        backend_factory = partial(STTWakeBackend, wake_profile=wake_profile,
                                  command_enabled=not args.wake_only and not capture_only)
    try:
        async with AsyncExitStack() as stack:
            inference = await stack.enter_async_context(SerialWorker("assistant-inference"))
            async with asyncio.timeout(15):
                backend = await inference.call(backend_factory)
                turn_vad = None
                if capture_only:
                    if vad_factory is None:
                        from .turn_vad import SileroVAD
                        vad_factory = SileroVAD
                    turn_vad = await inference.call(vad_factory)
            recorder = None
            if debug_recordings:
                from .turn_recording import TurnRecorder
                recorder = await inference.call(TurnRecorder)
            status(f"[ENGINE] Wake STT tiếng Việt trên CPU ({backend.startup_seconds:.2f}s).")
            if args.diagnostic:
                status(f"[SETTINGS] wake-profile={wake_profile}; reply-guard={reply_guard:g}s.")
            player = None
            if config.feedback_enabled and not args.no_feedback:
                playback = await stack.enter_async_context(SerialWorker("assistant-playback"))
                player = await playback.call(partial(player_factory, config.feedback_path,
                                             config.playback_device, echo_guard_seconds=reply_guard))
                playback.finalizer = player.close
            if calibration_frames:
                status("[AUDIO] Ổn định mic 2 giây, kiểm tra tín hiệu 1 giây.")
            pump = await stack.enter_async_context(AudioPump(config.device, buffer_ms=args.buffer_ms,
                                                            source_factory=source_factory))
            calibration = bytearray()
            while len(calibration) < calibration_frames * 640:
                frame = await pump.read()
                if isinstance(frame, AudioGap):
                    raise AudioError("Mất audio lúc kiểm tra microphone; chạy lại check-mic.")
                calibration.extend(frame.pcm)
            if calibration:
                stats = pcm_stats(calibration)
                if stats["clipped_percent"] > 1 or stats["rms"] < 5:
                    raise AudioError(f"Mic clipping hoặc gần như im lặng: {stats}; chạy check-mic.")
            runtime = AssistantRuntime(config, pump, inference, backend, playback, player,
                                       emit=emit, observe=observe, status=status,
                                       command_seconds=None if args.wake_only else args.command_seconds,
                                       show_wake_text=getattr(args, "show_wake_text", False),
                                       turn_vad=turn_vad, recorder=recorder)
            if initialized is not None:
                initialized.set()
            status(f"[READY ASSISTANT] Đang nghe ‘{config.wake_word}’; Ctrl+C để dừng.")
            interrupted = await runtime.run(stop, args.seconds)
        return runtime, interrupted
    finally:
        if runtime is not None:
            if (runtime.playing_context is not None and playback is not None
                    and not playback.thread.is_alive() and playback.error is None):
                observe(PlaybackStopped(runtime.playing_context, time.monotonic(), interrupted=True))
                runtime.playing_context = None
            if (runtime.machine.state == AssistantState.STOPPING and runtime.pump.closed
                    and not runtime.inference.thread.is_alive()
                    and (playback is None or not playback.thread.is_alive())):
                runtime.machine.transition(AssistantState.STOPPED, runtime.context)
            status(f"[STOP ASSISTANT] {runtime.trigger.events} wake; {runtime.cycles} chu kỳ; "
                   f"{runtime.acknowledgements} câu đáp; clipping={runtime.clipped}; "
                   f"bỏ={runtime.pump.dropped} frame; kết quả cũ={runtime.stale_results}; "
                   f"lệnh={runtime.commands}; timeout lệnh={runtime.command_timeouts}; "
                   f"lượt lệnh lỗi={runtime.command_aborts}.")
            if capture_only:
                status(f"[TURNS] {runtime.turns} lượt hoàn chỉnh; "
                       f"timeout={runtime.turn_timeouts}; lỗi={runtime.turn_aborts}.")
            if args.diagnostic:
                status(f"[CAPTURE] queue_peak={runtime.pump.max_pending}/{runtime.pump.capacity}; "
                       f"age_max={runtime.pump.max_observed_age:.3f}s; suppression={runtime.suppressed} frame; "
                       f"dropped={runtime.pump.drop_counts}.")


async def _command(config, args, emit, status):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = {kind: signal.getsignal(kind) for kind in (signal.SIGINT, signal.SIGTERM)}
    for kind in previous:
        loop.add_signal_handler(kind, stop.set)
    def observe(event):
        from .events import AssistantStateChanged
        if args.diagnostic and isinstance(event, AssistantStateChanged):
            status(f"[STATE] {event.previous} -> {event.current}")
    initialized = asyncio.Event()
    task = asyncio.create_task(session(config, args, emit, status, stop=stop, observe=observe,
                                       initialized=initialized))
    stopper = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if stopper.done() and not initialized.is_set():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            status("[STOP] Đã dừng khi đang khởi động.")
            if args.expect_events is not None:
                status("[TEST] INCOMPLETE: bài thử chưa bắt đầu.")
                return 130
            return 0
        runtime, interrupted = await task
    finally:
        stopper.cancel()
        await asyncio.gather(stopper, return_exceptions=True)
        for kind, handler in previous.items():
            loop.remove_signal_handler(kind)
            signal.signal(kind, handler)
    if interrupted and args.expect_events is not None:
        status("[TEST] INCOMPLETE: bài thử bị ngắt; không tính là PASS.")
        return 130
    if args.expect_events is not None:
        lost = runtime.pump.drop_counts["overflow"] + runtime.pump.drop_counts["stale"]
        passed = (runtime.trigger.events == args.expect_events
                  and runtime.cycles == runtime.trigger.events and runtime.clipped == 0
                  and lost == 0 and runtime.command_aborts == 0 and runtime.turn_aborts == 0)
        status(f"[TEST] {'PASS' if passed else 'FAIL'}: {runtime.trigger.events}/{args.expect_events} wake; "
               f"{runtime.cycles} chu kỳ hoàn tất; clipping={runtime.clipped}; mất={lost} frame.")
        return 0 if passed else 1
    return 0


def run(config, args, emit, status):
    if args.mock:
        from .mock_assistant import run_mock
        return asyncio.run(run_mock(config, args, emit, status))
    return asyncio.run(_command(config, args, emit, status))
