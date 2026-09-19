"""Unit tests for cooperative resource locks."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smart_hub.locks import (
    ResourceBusyError,
    ResourceLock,
    audio_lock,
    dataset_lock,
    eval_lock,
    gateway_lock,
)


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="smart-hub-test-locks-")
        self.lock_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_acquire_and_release(self):
        lock = ResourceLock("test_res", lock_dir=self.lock_dir)
        self.assertFalse(lock.is_locked())

        self.assertTrue(lock.acquire())
        self.assertTrue(lock.is_locked())

        # Second acquire without release must raise ResourceBusyError
        lock2 = ResourceLock("test_res", lock_dir=self.lock_dir)
        with self.assertRaises(ResourceBusyError):
            lock2.acquire(timeout=0.0)

        # Release first lock
        lock.release()
        self.assertFalse(lock.is_locked())

        # Now second lock can acquire
        self.assertTrue(lock2.acquire())
        lock2.release()

    def test_context_manager(self):
        lock = ResourceLock("ctx_res", lock_dir=self.lock_dir)
        with lock:
            self.assertTrue(lock.is_locked())
            lock2 = ResourceLock("ctx_res", lock_dir=self.lock_dir)
            with self.assertRaises(ResourceBusyError):
                lock2.acquire(timeout=0.0)

        self.assertFalse(lock.is_locked())


if __name__ == "__main__":
    unittest.main()
