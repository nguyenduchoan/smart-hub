"""Cooperative resource locks for audio, gateway, dataset, and evaluation.
Uses file-based locks (fcntl) to coordinate between processes (dashboard, CLI, workers).
"""
import contextlib
import errno
import fcntl
import os
from pathlib import Path
import time

from .config import ROOT

LOCKS_DIR = ROOT / ".local" / "dashboard" / "locks"


class ResourceBusyError(Exception):
    """Raised when a requested resource is currently acquired by another process/job."""
    def __init__(self, resource_name: str, message: str = ""):
        self.resource_name = resource_name
        super().__init__(message or f"Tài nguyên '{resource_name}' đang bận. Vui lòng thử lại sau.")


class ResourceLock:
    """File-based lock using fcntl.flock."""

    def __init__(self, name: str, lock_dir: Path | None = None):
        self.name = name
        self.lock_dir = Path(lock_dir) if lock_dir else LOCKS_DIR
        self.lock_file = self.lock_dir / f"{name}.lock"
        self._fd = None

    def acquire(self, timeout: float = 0.0) -> bool:
        """Acquire the lock. If timeout == 0, non-blocking attempt.
        If timeout > 0, retry until timeout expires.
        Returns True if acquired, raises ResourceBusyError if unable to acquire.
        """
        if self._fd is not None:
            return True

        old_umask = os.umask(0o077)
        try:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR, 0o600)
        finally:
            os.umask(old_umask)

        start = time.monotonic()
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Write current pid
                try:
                    os.ftruncate(self._fd, 0)
                    os.write(self._fd, f"{os.getpid()} {time.time()}\n".encode("utf-8"))
                except OSError:
                    pass
                return True
            except (BlockingIOError, OSError) as exc:
                if isinstance(exc, OSError) and exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() - start >= timeout:
                    if self._fd is not None:
                        os.close(self._fd)
                        self._fd = None
                    raise ResourceBusyError(self.name)
                time.sleep(0.1)

    def release(self):
        """Release lock and close fd."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def is_locked(self) -> bool:
        """Check if resource is currently held without blocking."""
        try:
            old_umask = os.umask(0o077)
            try:
                self.lock_dir.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR, 0o600)
            finally:
                os.umask(old_umask)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            except (BlockingIOError, OSError):
                return True
            finally:
                os.close(fd)
        except OSError:
            return False

    def __enter__(self):
        self.acquire(timeout=0.0)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def audio_lock(timeout: float = 0.0) -> ResourceLock:
    """Cooperative lock for host microphone and audio playback."""
    lock = ResourceLock("audio_capture")
    lock.acquire(timeout=timeout)
    return lock


def gateway_lock(gateway_id: str, timeout: float = 0.0) -> ResourceLock:
    """Cooperative lock for a specific Broadlink gateway."""
    safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in gateway_id)
    lock = ResourceLock(f"gateway_{safe_id}")
    lock.acquire(timeout=timeout)
    return lock


def eval_lock(timeout: float = 0.0) -> ResourceLock:
    """Cooperative lock for offline benchmark runner."""
    lock = ResourceLock("wake_eval")
    lock.acquire(timeout=timeout)
    return lock


def dataset_lock(timeout: float = 5.0) -> ResourceLock:
    """Cooperative lock for child-study dataset metadata updates."""
    lock = ResourceLock("child_study_dataset")
    lock.acquire(timeout=timeout)
    return lock
