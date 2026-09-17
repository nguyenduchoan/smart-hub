import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from smart_hub.feedback import VoiceFeedback, feed_audio


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "reply.wav"
        with wave.open(str(self.path), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x00\x00" * 12000)
        self.now = 10.0
        self.player = VoiceFeedback(self.path, clock=lambda: self.now)
        self.addCleanup(self.player.close)
        self.process = Mock()
        self.process.poll.return_value = None
        self.process.stderr = io.BytesIO()
        self.popen = patch("smart_hub.feedback.subprocess.Popen", return_value=self.process).start()
        self.addCleanup(patch.stopall)

    def test_one_playback_for_repeated_callbacks_while_busy(self):
        self.player.play()
        self.player.play()
        self.popen.assert_called_once()
        self.assertEqual(self.popen.call_args.args[0],
                         ["aplay", "-q", "-D", "pipewire", str(self.path)])

    def test_capture_is_drained_and_not_recognized_during_reply_and_echo(self):
        detector = Mock()
        frame = b"\x01\x00" * 320
        self.player.play()
        feed_audio(detector, frame, self.player)
        self.process.poll.return_value = 0
        feed_audio(detector, frame, self.player)
        self.now += 0.5
        feed_audio(detector, frame, self.player)
        self.assertEqual(detector.discard.call_count, 3)
        detector.feed.assert_not_called()
        self.now += 0.3
        feed_audio(detector, frame, self.player)
        detector.feed.assert_called_once_with(frame)

    def test_failed_playback_reports_error_and_releases_pipe(self):
        self.player.play()
        self.process.stderr.write(b"audio device unavailable")
        self.process.stderr.seek(0)
        self.process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "audio device unavailable"):
            self.player.suppressing()
        self.assertIsNone(self.player.process)
        self.assertTrue(self.process.stderr.closed)

    def test_shorter_guard_accepts_command_soon_after_playback_without_replaying_echo(self):
        player = VoiceFeedback(self.path, clock=lambda: self.now, echo_guard_seconds=0.1)
        self.addCleanup(player.close)
        detector = Mock()
        frame = b"\x01\0" * 320
        player.play()
        feed_audio(detector, frame, player)
        self.process.poll.return_value = 0
        feed_audio(detector, frame, player)
        self.now += 0.05
        feed_audio(detector, frame, player)
        self.assertEqual(detector.discard.call_count, 3)
        detector.feed.assert_not_called()
        self.now += 0.07
        feed_audio(detector, frame, player)
        detector.feed.assert_called_once_with(frame)

    def test_invalid_echo_guard_is_rejected_before_audio_opens(self):
        for value in (-0.1, 2.1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                VoiceFeedback(self.path, echo_guard_seconds=value)
        self.popen.assert_not_called()

    def test_stalled_player_is_killed_after_bounded_timeout(self):
        self.player.play()
        self.process.wait.side_effect = [subprocess.TimeoutExpired("aplay", 2), 0]
        self.now += 4
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.player.suppressing()
        self.process.terminate.assert_called_once()
        self.process.kill.assert_called_once()
        self.assertIsNone(self.player.process)
        self.assertTrue(self.process.stderr.closed)

    def test_disabled_feedback_passes_audio_directly(self):
        detector = Mock()
        frame = b"\x00" * 640
        feed_audio(detector, frame)
        detector.feed.assert_called_once_with(frame)
        detector.discard.assert_not_called()

    def test_invalid_wav_fails_before_starting_player(self):
        self.path.write_bytes(b"not a WAV file")
        with self.assertRaisesRegex(ValueError, "WAV PCM"):
            VoiceFeedback(self.path)
        self.popen.assert_not_called()
