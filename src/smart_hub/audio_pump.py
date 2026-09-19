"""One capture owner; bounded PCM, coalesced gaps and loop notifications."""
import asyncio
from collections import deque
from dataclasses import replace
import threading
import time

from .audio import AlsaCapture, AudioError, FRAME_BYTES, FRAME_SAMPLES
from .events import AudioFrame, AudioGap


class AudioPump:
    def __init__(self, device="pipewire", *, buffer_ms=500, source_factory=AlsaCapture,
                 clock=time.monotonic):
        if type(buffer_ms) is not int or not 20 <= buffer_ms <= 500 or buffer_ms % 20:
            raise ValueError("Buffer cần 20–500 ms, theo bội số 20 ms.")
        self.device, self.source_factory, self.clock = device, source_factory, clock
        self.capacity, self.max_age = buffer_ms // 20, buffer_ms / 1000
        self.lock = threading.Lock()
        self.frames = deque()
        self.gap = None
        self.epoch = 0
        self.error = None
        self.source = None
        self.ready = False
        self.closed = False
        self.stop = threading.Event()
        self.thread = None
        self.callback_pending = False
        self.wake_set = False
        self.max_pending = self.dropped = self.notifications = 0
        self.max_observed_age = 0.0
        self.drop_counts = {"overflow": 0, "stale": 0, "playback": 0}

    def _notify_locked(self, force=False):
        if not self.callback_pending and (force or not self.wake_set) and not self.closed:
            self.callback_pending = True
            self.notifications += 1
            self.loop.call_soon_threadsafe(self._notify_loop)

    def _notify_loop(self):
        with self.lock:
            self.callback_pending = False
            self.wake_set = True
            self.changed.set()
            if self.error is not None and not self.failed.done():
                self.failed.set_result(self.error)

    def _drop_locked(self, reason):
        frame = self.frames.popleft()
        if self.gap is None:
            self.gap = AudioGap(frame.sample_offset, frame.end_sample, 1, (reason,))
        else:
            self.gap = AudioGap(self.gap.start_sample, frame.end_sample,
                                self.gap.dropped_frames + 1,
                                tuple(sorted(set((*self.gap.reasons, reason)))))
        self.dropped += 1
        self.drop_counts[reason] += 1
        self.epoch += 1  # Invalidate even an inference already running off-loop.

    def _publish(self, frame):
        with self.lock:
            if len(self.frames) == self.capacity:
                self._drop_locked("overflow")
            self.frames.append(frame)
            self.max_pending = max(self.max_pending, len(self.frames))
            self._notify_locked()

    def _fail(self, error):
        with self.lock:
            if self.error is None:
                self.error = error
                # Failure has its own future, even when PCM is already signalled.
                self._notify_locked(force=True)

    def _capture(self):
        res_lock = None
        try:
            is_real_capture = (self.source_factory is AlsaCapture or getattr(self.source_factory, "__name__", "") == "AlsaCapture")
            if is_real_capture:
                from .locks import audio_lock, ResourceBusyError, ResourceLock
                if ResourceLock("wake_eval").is_locked():
                    raise AudioError("Tiến trình benchmark đánh giá model đang chạy; không thể capture mic.")
                try:
                    res_lock = audio_lock(timeout=0.0)
                except ResourceBusyError as exc:
                    raise AudioError(f"Microphone đang bận: {exc}")

            self.source = self.source_factory(self.device)
            with self.source as source:
                try:
                    with self.lock:
                        self.ready = True
                        self._notify_locked()
                    sequence = 0
                    while not self.stop.is_set():
                        pcm = source.read_frame()
                        if self.stop.is_set():
                            break
                        if len(pcm) != FRAME_BYTES:
                            raise AudioError("Capture trả frame khác PCM 20 ms.")
                        self._publish(AudioFrame(sequence, sequence * FRAME_SAMPLES, self.clock(), pcm))
                        sequence += 1
                except Exception as exc:
                    if not self.stop.is_set():
                        self._fail(exc)  # Visible BEFORE slow source cleanup.
                        raise
                    # request_stop deliberately unblocks read_frame with EOF/error.
                    # Leave the context normally so a cleanup error stays distinct.
        except Exception as exc:
            if self.ready or not self.stop.is_set():
                self._fail(exc)
        finally:
            if res_lock is not None:
                res_lock.release()

    async def __aenter__(self):
        self.loop = asyncio.get_running_loop()
        self.changed = asyncio.Event()
        self.failed = self.loop.create_future()
        self.thread = threading.Thread(target=self._capture, name="assistant-capture", daemon=True)
        self.thread.start()
        try:
            async with asyncio.timeout(10):
                while True:
                    with self.lock:
                        self.check_health()
                        if self.ready:
                            break
                        self._arm_locked()
                    await self.changed.wait()
        except BaseException:
            await self.__aexit__()
            raise
        return self

    def check_health(self):
        if self.error is not None:
            raise self.error

    def _arm_locked(self):
        # Keep callback_pending intact: a scheduled callback may not have run yet.
        self.wake_set = False
        self.changed.clear()

    async def read(self):
        while True:
            with self.lock:
                self.check_health()
                if self.closed or self.stop.is_set():
                    raise AudioError("AudioPump đã đóng.")
                now = self.clock()
                while self.frames:
                    age = max(0, now - self.frames[0].captured_at)
                    self.max_observed_age = max(self.max_observed_age, age)
                    if age <= self.max_age:
                        break
                    self._drop_locked("stale")
                if self.gap is not None:
                    gap, self.gap = self.gap, None
                    return gap
                if self.frames:
                    return replace(self.frames.popleft(), continuity_id=self.epoch)
                self._arm_locked()
            await self.changed.wait()

    def discard_pending(self, reason="playback"):
        with self.lock:
            while self.frames:
                self._drop_locked(reason)

    async def __aexit__(self, exc_type=None, *_):
        self.stop.set()
        if self.source is not None:
            request_stop = getattr(self.source, "request_stop", None)
            if request_stop:
                request_stop()
        if self.thread is not None:
            await asyncio.to_thread(self.thread.join, 8)
            if self.thread.is_alive():
                raise AudioError("Capture không dừng trong 8 giây.")
        with self.lock:
            self.closed = True
            self.frames.clear()
            self.gap = None
            self.changed.set()
        if exc_type is None:
            self.check_health()
