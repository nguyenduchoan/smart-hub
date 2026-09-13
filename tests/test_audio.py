import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from smart_hub.audio import AlsaCapture, AudioError, FRAME_BYTES, list_inputs, pcm_stats, wav_frames


class AudioTests(unittest.TestCase):
    def test_1_no_soundcards_is_failure_even_if_arecord_returns_zero(self):
        with patch("smart_hub.audio.shutil.which", return_value="/usr/bin/arecord"), patch(
                "smart_hub.audio.subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 0, "", "arecord: no soundcards found")):
            with self.assertRaises(AudioError):
                list_inputs()

    def test_2_partial_capture_reads_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = Path(directory) / "arecord"
            recorder.write_text("#!/usr/bin/python3\nimport os,time\nos.write(1,b'\\x00')\ntime.sleep(.05)\nos.write(1,b'\\x00'*639)\ntime.sleep(20)\n")
            recorder.chmod(0o755)
            with patch.dict(os.environ, {"PATH": directory}):
                with AlsaCapture(frame_timeout=1, warmup_seconds=0) as capture:
                    self.assertEqual(len(capture.read_frame()), FRAME_BYTES)
                    child = capture.process
                self.assertIsNotNone(child.poll())

    def test_2_stalled_capture_times_out_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = Path(directory) / "arecord"
            recorder.write_text("#!/usr/bin/python3\nimport time\ntime.sleep(20)\n")
            recorder.chmod(0o755)
            with patch.dict(os.environ, {"PATH": directory}):
                with AlsaCapture(frame_timeout=0.15, warmup_seconds=0) as capture:
                    with self.assertRaisesRegex(AudioError, "timeout"):
                        capture.read_frame()
                    child = capture.process
                self.assertIsNotNone(child.poll())

    def test_2_startup_transient_is_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = Path(directory) / "arecord"
            recorder.write_text("#!/usr/bin/python3\nimport os,time\nos.write(1,b'\\xff\\x7f'*640+b'\\x00'*640)\ntime.sleep(20)\n")
            recorder.chmod(0o755)
            with patch.dict(os.environ, {"PATH": directory}):
                with AlsaCapture(frame_timeout=1, warmup_seconds=0.04) as capture:
                    self.assertEqual(capture.read_frame(), b"\x00" * FRAME_BYTES)

    def test_wav_wrong_rate_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x00" * 16000)
            with self.assertRaises(AudioError):
                list(wav_frames(path))

    def test_2_clipping_detects_both_signed_limits(self):
        stats = pcm_stats(b"\xff\x7f\x00\x80" * 100)
        self.assertEqual(stats["clipped_percent"], 100)
        self.assertEqual(stats["peak"], 32768)
