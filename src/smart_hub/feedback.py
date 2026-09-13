"""Play a fixed local acknowledgement without blocking microphone capture."""
from pathlib import Path
import subprocess
import time
import wave


class VoiceFeedback:
    def __init__(self, path, device="pipewire", clock=time.monotonic):
        self.path = Path(path)
        self.device = device
        self.clock = clock
        self.process = None
        self.until = 0
        self.started = 0
        try:
            with wave.open(str(self.path), "rb") as wav:
                self.duration = wav.getnframes() / wav.getframerate()
                if wav.getcomptype() != "NONE" or not 0 < self.duration <= 5:
                    raise ValueError("Phản hồi cần WAV PCM dài tối đa 5 giây.")
        except (wave.Error, EOFError) as exc:
            raise ValueError(f"File phản hồi không phải WAV PCM hợp lệ: {self.path}") from exc

    def play(self):
        if self.suppressing():
            return
        self.process = subprocess.Popen(
            ["aplay", "-q", "-D", self.device, str(self.path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.started = self.clock()

    def suppressing(self):
        if self.process is not None:
            result = self.process.poll()
            if result is None:
                if self.clock() - self.started > self.duration + 3:
                    self.close()
                    raise RuntimeError("Playback phản hồi timeout.")
                return True
            error = self.process.stderr.read().decode(errors="replace").strip()
            self.process.stderr.close()
            self.process = None
            if result:
                raise RuntimeError(f"Không phát được phản hồi: {error}")
            self.until = self.clock() + 0.7  # Allow room echo to decay.
        return self.clock() < self.until

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process.stderr.close()
            self.process = None


def feed_audio(detector, frame, player=None):
    if player is not None and player.suppressing():
        detector.discard(frame)
    else:
        detector.feed(frame)
