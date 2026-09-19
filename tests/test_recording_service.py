"""Unit tests for recording service state machine, manual advance, and session safety."""
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

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

    def test_r05_capture_before_cue_and_drain(self):
        from unittest import mock
        events = []

        class MockCapture:
            def __enter__(self):
                events.append("enter_capture")
                return self

            def __exit__(self, *args):
                events.append("exit_capture")

            def drain(self):
                events.append("drain_capture")

            def read_frame(self):
                events.append("read_frame")
                return b"\x00" * 640

        cfg = SessionConfig(
            speaker="child",
            speaker_id="child_r05",
            split="pilot",
            label="positive",
            phrase="Maika ơi",
            expected_events=1,
            distance_m=1.0,
            condition="quiet",
            takes_planned=1,
            manual_advance=False,
            no_sync=True,
            mock=False,
        )

        with mock.patch("smart_hub.recording.service.AlsaCapture", return_value=MockCapture()), \
             mock.patch.object(self.service, "_play_cue_tone", side_effect=lambda capture=None: events.append("play_cue")):
            self.service.start_session(cfg)
            start = time.monotonic()
            while self.service.state != RecordingState.COMPLETED and time.monotonic() - start < 3.0:
                time.sleep(0.02)

        self.assertEqual(self.service.state, RecordingState.COMPLETED)
        # Check order: enter_capture must happen BEFORE play_cue
        self.assertIn("enter_capture", events)
        self.assertIn("play_cue", events)
        self.assertIn("drain_capture", events)
        self.assertLess(events.index("enter_capture"), events.index("play_cue"))

    def test_v2_01_alsa_capture_drain_with_raw_unbuffered_pipe(self):
        import os
        import selectors
        from smart_hub.audio import AlsaCapture

        # Create raw OS pipe
        r_fd, w_fd = os.pipe()
        try:
            # FileIO object without .read1() method (same as Popen bufsize=0)
            r_file = os.fdopen(r_fd, "rb", buffering=0)
            self.assertFalse(hasattr(r_file, "read1"), "Raw FileIO must not have read1 method")

            cap = AlsaCapture("default")
            cap.selector = selectors.DefaultSelector()
            cap.selector.register(r_file, selectors.EVENT_READ, "pcm")

            class DummyProc:
                stdout = r_file
                stderr = None
                def poll(self):
                    return None

            cap.process = DummyProc()
            # Write 2048 bytes of PCM into pipe
            os.write(w_fd, b"\x00" * 2048)

            # drain() must finish promptly (<0.5s) without hanging or raising AttributeError
            start = time.monotonic()
            cap.drain(timeout=0.2)
            elapsed = time.monotonic() - start

            self.assertLess(elapsed, 0.5)
            # Verify selector now has 0 unread bytes
            events = cap.selector.select(0.0)
            self.assertEqual(len(events), 0)
        finally:
            try:
                cap.selector.close()
            except Exception:
                pass
            r_file.close()
            os.close(w_fd)

    def test_v2_10_cue_playback_failure_halts_session(self):
        from smart_hub.audio import AudioError
        cfg = SessionConfig(
            speaker="child",
            speaker_id="child_cue_err",
            split="pilot",
            label="positive",
            phrase="Maika ơi",
            expected_events=1,
            distance_m=1.0,
            condition="quiet",
            takes_planned=2,
            manual_advance=False,
            no_sync=True,
            mock=False,
        )
        with mock.patch("smart_hub.recording.service.AlsaCapture"), \
             mock.patch.object(self.service, "_play_cue_tone", side_effect=AudioError("aplay failed")):
            self.service.start_session(cfg)
            start = time.monotonic()
            while self.service.state != RecordingState.FAILED and time.monotonic() - start < 3.0:
                time.sleep(0.02)

        self.assertEqual(self.service.state, RecordingState.FAILED)
        self.assertIn("aplay failed", self.service.error_message)
        self.assertEqual(len(self.service.clips), 0)

    def test_v2_11_sync_failure_sets_failed_state(self):
        cfg = SessionConfig(
            speaker="child",
            speaker_id="child_sync_fail",
            split="pilot",
            label="positive",
            phrase="Maika ơi",
            expected_events=1,
            distance_m=1.0,
            condition="quiet",
            takes_planned=1,
            manual_advance=False,
            no_sync=False,
            mock=True,
        )
        with mock.patch.object(self.service, "_sync_child_study", side_effect=OSError("disk full")):
            self.service.start_session(cfg)
            start = time.monotonic()
            while self.service.state != RecordingState.FAILED and time.monotonic() - start < 3.0:
                time.sleep(0.02)

        self.assertEqual(self.service.state, RecordingState.FAILED)
        self.assertIn("disk full", self.service.error_message)
        self.assertEqual(self.service.manifest["status"], "sync_failed")


if __name__ == "__main__":
    unittest.main()
