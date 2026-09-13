"""ALSA CLI capture through the existing PipeWire plugin; no config writes."""
from array import array
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import time
import wave

RATE = 16000
SAMPLE_BYTES = 2
FRAME_SAMPLES = 320  # 20 ms
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_BYTES


class AudioError(RuntimeError):
    pass


def list_inputs():
    if not shutil.which("arecord"):
        raise AudioError("Thiếu arecord (alsa-utils).")
    result = subprocess.run(["arecord", "-l"], capture_output=True, text=True,
                            timeout=5, env={**os.environ, "LC_ALL": "C"})
    cards = [line.strip() for line in result.stdout.splitlines()
             if re.match(r"^card \d+:", line)]
    if result.returncode or not cards:
        detail = result.stderr.strip() or result.stdout.strip()
        raise AudioError(f"Không thấy thiết bị capture ALSA: {detail}. "
                         "Kiểm tra quyền audio hoặc chạy ngoài sandbox.")
    return cards


class AlsaCapture:
    def __init__(self, device="pipewire", frame_timeout=3.0, warmup_seconds=2.0):
        if not device or device == "null":
            raise AudioError("Cần thiết bị capture thật, không dùng ALSA null.")
        self.device = device
        self.frame_timeout = frame_timeout
        self.warmup_seconds = warmup_seconds
        self.process = None
        self.selector = None
        self.buffer = bytearray()
        self.error_tail = bytearray()

    def __enter__(self):
        self.process = subprocess.Popen(
            ["arecord", "-q", "-D", self.device, "-t", "raw", "-f", "S16_LE",
             "-r", str(RATE), "-c", "1"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, env={**os.environ, "LC_ALL": "C"},
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "pcm")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "error")
        try:
            # This ALC256 produces a large DC transient when capture opens.
            # Drop startup PCM rather than training on it or muting true errors.
            for _ in range(math.ceil(self.warmup_seconds * RATE / FRAME_SAMPLES)):
                self.read_frame()
        except BaseException:
            self.__exit__()
            raise
        return self

    def read_frame(self):
        deadline = time.monotonic() + self.frame_timeout
        while len(self.buffer) < FRAME_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AudioError("Capture timeout: microphone không trả audio. "
                                 + self.error_tail.decode(errors="replace"))
            for key, _ in self.selector.select(remaining):
                block = os.read(key.fd, FRAME_BYTES if key.data == "pcm" else 4096)
                if key.data == "error":
                    if not block:
                        self.selector.unregister(key.fileobj)
                        continue
                    self.error_tail.extend(block)
                    self.error_tail[:] = self.error_tail[-4096:]
                    # Quiet arecord should not emit diagnostics during a healthy stream.
                    raise AudioError("arecord: " + self.error_tail.decode(errors="replace").strip())
                if not block:
                    raise AudioError("Microphone ngắt kết nối hoặc arecord dừng. "
                                     + self.error_tail.decode(errors="replace"))
                self.buffer.extend(block)
        frame = bytes(self.buffer[:FRAME_BYTES])
        del self.buffer[:FRAME_BYTES]
        return frame

    def __exit__(self, *_):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            for stream in (self.process.stdout, self.process.stderr):
                stream.close()
        if self.selector:
            self.selector.close()


def capture_pcm(device, seconds):
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError("Capture kiểm thử cần 0 < seconds <= 300.")
    chunks = []
    with AlsaCapture(device) as source:
        for _ in range(math.ceil(seconds * RATE / FRAME_SAMPLES)):
            chunks.append(source.read_frame())
    return b"".join(chunks)[:round(seconds * RATE) * SAMPLE_BYTES]


def pcm_stats(pcm):
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise AudioError("Capture rỗng.")
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    return {"seconds": len(samples) / RATE, "samples": len(samples),
            "peak": max(abs(x) for x in samples), "rms": round(rms, 2),
            "clipped_percent": round(100 * sum(abs(x) >= 32767 for x in samples) / len(samples), 3)}


def wav_frames(path):
    with wave.open(str(Path(path)), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (RATE, 1, 2, "NONE"):
            raise AudioError("WAV cần PCM 16 kHz, mono, signed 16-bit.")
        expected = source.getnframes() * SAMPLE_BYTES
        read = 0
        while frame := source.readframes(FRAME_SAMPLES):
            read += len(frame)
            yield frame
        if read != expected or read == 0:
            raise AudioError("WAV rỗng hoặc bị cắt cụt.")
