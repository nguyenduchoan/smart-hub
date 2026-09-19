"""Unit tests for recording service state machine, manual advance, and session safety."""
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smart_hub.recording import (
    RecordingService,
    RecordingState,
    SessionConfig,
)


class RecordingServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = RecordingService()

    def tearDown(self):
        self.service.stop()
        if self.service._thread and self.service._thread.is_alive():
            self.service._thread.join(timeout=2.0)
        if self.service._audio_res_lock:
            self.service._audio_res_lock.release()
            self.service._audio_res_lock = None

    def test_session_lifecycle_with_manual_advance(self):
        cfg = SessionConfig(
            speaker="child",
            speaker_id="child_test",
            split="pilot",
            label="positive",
            phrase="Maika ơi",
            expected_events=1,
            distance_m=1.0,
            condition="quiet",
            takes_planned=2,
            manual_advance=True,
            no_sync=True,
            mock=True,
        )
        self.service.start_session(cfg)

        # Wait for state to reach WAITING_USER (take 1)
        start = time.monotonic()
        while self.service.state != RecordingState.WAITING_USER and time.monotonic() - start < 3.0:
            time.sleep(0.02)
        self.assertEqual(self.service.state, RecordingState.WAITING_USER)
        self.assertEqual(self.service.current_take, 1)

        # Advance take 1
        self.service.advance()

        # Wait for worker to leave WAITING_USER of take 1
        start = time.monotonic()
        while self.service.state == RecordingState.WAITING_USER and time.monotonic() - start < 1.0:
            time.sleep(0.02)

        # Wait for take 1 to record and process, then reach WAITING_USER (take 2)
        start = time.monotonic()
        while self.service.state != RecordingState.WAITING_USER and time.monotonic() - start < 4.0:
            time.sleep(0.02)
        self.assertEqual(self.service.state, RecordingState.WAITING_USER)
        self.assertEqual(self.service.current_take, 2)
        self.assertEqual(len(self.service.clips), 1)

        # Advance take 2
        self.service.advance()

        # Wait for session completion
        start = time.monotonic()
        while self.service.state != RecordingState.COMPLETED and time.monotonic() - start < 4.0:
            time.sleep(0.02)
        self.assertEqual(self.service.state, RecordingState.COMPLETED)
        self.assertEqual(len(self.service.clips), 2)
        self.assertEqual(self.service.manifest["status"], "captured_pending_review")

    def test_stop_session_midway_preserves_completed_takes(self):
        cfg = SessionConfig(
            speaker="adult",
            speaker_id="adult_test",
            split="dev",
            label="positive",
            phrase="Maika ơi",
            expected_events=1,
            distance_m=1.0,
            condition="quiet",
            takes_planned=3,
            manual_advance=True,
            no_sync=True,
            mock=True,
        )
        self.service.start_session(cfg)

        # Wait for WAITING_USER take 1
        while self.service.state != RecordingState.WAITING_USER:
            time.sleep(0.05)

        # Complete take 1
        self.service.advance()

        # Wait for worker to leave WAITING_USER of take 1
        start = time.monotonic()
        while self.service.state == RecordingState.WAITING_USER and time.monotonic() - start < 1.0:
            time.sleep(0.02)

        # Wait for WAITING_USER take 2
        while self.service.state != RecordingState.WAITING_USER:
            time.sleep(0.02)

        # Stop now!
        self.service.stop()

        start = time.monotonic()
        while self.service.state not in (RecordingState.INTERRUPTED, RecordingState.IDLE) and time.monotonic() - start < 3.0:
            time.sleep(0.05)

        self.assertEqual(self.service.state, RecordingState.INTERRUPTED)
        self.assertEqual(len(self.service.clips), 1)
        self.assertEqual(self.service.manifest["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
