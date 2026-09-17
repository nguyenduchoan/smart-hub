"""Opt-in debug WAVs, private files under the ignored recordings directory."""
import os
from pathlib import Path
import tempfile
import uuid
import wave

from .config import ROOT


class TurnRecorder:
    def __init__(self, directory=ROOT / "recordings"):
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="turns-", dir=directory))

    def write(self, event):
        path = self.directory / f"{uuid.uuid4().hex}.wav"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                with wave.open(stream, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(event.sample_rate)
                    audio.writeframes(event.pcm)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path
