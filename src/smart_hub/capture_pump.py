"""One capture owner with a bounded 500 ms handoff for the STT prototype."""
import queue
import threading
import time

from .audio import AlsaCapture, AudioError, FRAME_BYTES


class CapturePump:
    def __init__(self, device="pipewire", source_factory=AlsaCapture, capacity=25):
        if not isinstance(capacity, int) or not 1 <= capacity <= 25:
            raise ValueError("Capture queue cần 1–25 frame (tối đa 500 ms).")
        self.device = device
        self.source_factory = source_factory
        self.queue = queue.Queue(maxsize=capacity)
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.thread = None
        self.max_pending = 0

    def _run(self):
        try:
            with self.source_factory(self.device) as source:
                try:
                    self.ready.set()
                    while not self.stop.is_set():
                        frame = source.read_frame()
                        if self.stop.is_set():
                            break
                        if len(frame) != FRAME_BYTES:
                            raise AudioError("Capture trả frame sai độ dài.")
                        try:
                            self.queue.put_nowait((time.monotonic(), frame))
                        except queue.Full:
                            raise AudioError("STT không theo kịp capture: buffer 500 ms đã đầy; phiên thử FAIL.") from None
                        self.max_pending = max(self.max_pending, self.queue.qsize())
                except Exception as exc:
                    # Publish failure before potentially slow arecord cleanup. A
                    # concurrent decode must not emit while this stream is failing.
                    self.error = exc
                    raise
        except Exception as exc:
            # Error signalling is independent of the PCM queue, including overflow.
            if self.error is None:
                self.error = exc
        finally:
            self.ready.set()

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, name="stt-capture", daemon=True)
        self.thread.start()
        try:
            if not self.ready.wait(timeout=10):
                raise AudioError("Timeout khi mở microphone.")
            self.check_health()
        except BaseException:
            self.__exit__()
            raise
        return self

    def check_health(self):
        if self.error is not None:
            raise self.error

    def read_frame(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.check_health()
            try:
                captured_at, frame = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self.check_health()
            if time.monotonic() - captured_at > 0.5:
                raise AudioError("Audio tồn đọng quá 500 ms; dừng để không nhận từ dữ liệu cũ.")
            return frame
        raise AudioError("Capture pump timeout.")

    def __exit__(self, exc_type=None, *_):
        self.stop.set()
        if self.thread:
            # AlsaCapture bounds read timeout (3 s) and child cleanup (2 + 2 s).
            self.thread.join(timeout=8)
            if self.thread.is_alive():
                raise AudioError("Capture thread chưa đóng trong 8 giây.")
        if exc_type is None:
            self.check_health()
