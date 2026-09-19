"""Recording service and state machine for adult/child voice study sessions.
Shared between CLI and web dashboard.
"""
from array import array
import contextlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
import wave

from ..audio import (
    AlsaCapture,
    AudioError,
    RATE,
    pcm_stats,
)
from ..child_study import (
    CHILD_STUDY_DIR,
    NEGATIVE_PRESETS,
    create_label_entry,
    ensure_child_study_dirs,
    load_sessions,
    save_session_and_labels,
)
from ..config import ROOT, load_config
from ..locks import audio_lock, ResourceBusyError


class RecordingState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    WAITING_USER = "waiting_user"
    CUE = "cue"
    RECORDING = "recording"
    PROCESSING = "processing"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass
class TakeClip:
    take_number: int
    file_name: str
    recorded_at: str
    peak: int
    rms: float
    clipped_percent: float
    sha256: str
    duration_s: float = 5.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SessionConfig:
    speaker: str                 # 'adult' or 'child'
    speaker_id: str
    split: str                   # 'pilot', 'dev', 'test'
    label: str                   # 'positive' or 'negative'
    phrase: str
    expected_events: int
    distance_m: float
    condition: str
    takes_planned: int
    manual_advance: bool = True
    session_id: Optional[str] = None
    no_sync: bool = False
    mock: bool = False


class RecordingService:
    """Manages an active recording session with strict state machine and audio lock."""

    def __init__(self, config=None, audio_device: Optional[str] = None, playback_device: Optional[str] = None):
        self.app_config = config or load_config()
        self.audio_device = audio_device or self.app_config.device
        self.playback_device = playback_device or self.app_config.playback_device

        self.state: RecordingState = RecordingState.IDLE
        self.session_config: Optional[SessionConfig] = None
        self.session_dir: Optional[Path] = None
        self.manifest: Dict[str, Any] = {}
        self.clips: List[TakeClip] = []
        self.current_take: int = 0
        self.error_message: Optional[str] = None

        self._thread: Optional[threading.Thread] = None
        self._advance_event = threading.Event()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._audio_res_lock = None

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state.value,
                "session_id": self.manifest.get("session_id"),
                "current_take": self.current_take,
                "takes_planned": self.session_config.takes_planned if self.session_config else 0,
                "speaker_id": self.session_config.speaker_id if self.session_config else None,
                "phrase": self.session_config.phrase if self.session_config else None,
                "split": self.session_config.split if self.session_config else None,
                "label": self.session_config.label if self.session_config else None,
                "clips": [c.to_dict() for c in self.clips],
                "error": self.error_message,
                "directory": str(self.session_dir) if self.session_dir else None,
            }

    def start_session(self, session_cfg: SessionConfig):
        with self._lock:
            if self.state not in (RecordingState.IDLE, RecordingState.COMPLETED, RecordingState.INTERRUPTED, RecordingState.FAILED):
                raise RuntimeError(f"Cannot start session; current state is {self.state.value}")

            # Acquire audio lock
            try:
                self._audio_res_lock = audio_lock(timeout=0.0)
            except ResourceBusyError as exc:
                raise RuntimeError(f"Microphone đang bận hoặc được tiến trình khác sử dụng: {exc}")

            try:
                self.session_config = session_cfg
                self.clips = []
                self.current_take = 0
                self.error_message = None
                self._advance_event.clear()
                self._stop_event.clear()

                # Generate session ID if needed
                now = datetime.now()
                if session_cfg.session_id:
                    sid = session_cfg.session_id.strip()
                    existing = load_sessions()
                    if any(s.get("session_id") == sid for s in existing):
                        raise ValueError(f"Mã phiên thu '{sid}' đã tồn tại trong sessions.json.")
                    self.session_id = sid
                else:
                    self.session_id = f"S_{now.strftime('%Y%m%d_%H%M%S_%f')}_{session_cfg.speaker}"

                # Create output directory
                recordings_root = ROOT / "recordings"
                recordings_root.mkdir(exist_ok=True)
                self.session_dir = Path(
                    tempfile.mkdtemp(
                        prefix=now.strftime("%Y%m%d-%H%M%S-") + session_cfg.speaker + "-",
                        dir=recordings_root,
                    )
                )

                self.manifest = {
                    "session_id": self.session_id,
                    "split": session_cfg.split,
                    "speaker_label": session_cfg.speaker,
                    "speaker_id": session_cfg.speaker_id,
                    "label": session_cfg.label,
                    "expected_phrase": session_cfg.phrase,
                    "expected_events": session_cfg.expected_events,
                    "distance_m": session_cfg.distance_m,
                    "condition": session_cfg.condition,
                    "speaker_confirmed": False,
                    "created_at": now.astimezone().isoformat(),
                    "device": self.audio_device,
                    "sample_rate": RATE,
                    "status": "incomplete",
                    "clips": [],
                }
                self._save_manifest()

                self.state = RecordingState.PREPARING
                self._thread = threading.Thread(target=self._run_session_worker, daemon=True)
                self._thread.start()
            except Exception:
                if self._audio_res_lock:
                    self._audio_res_lock.release()
                    self._audio_res_lock = None
                raise

    def advance(self):
        """User triggers next take in manual advance mode."""
        with self._lock:
            if self.state != RecordingState.WAITING_USER:
                raise RuntimeError(f"Chỉ có thể bấm lượt tiếp theo khi trạng thái là 'waiting_user' (hiện tại: {self.state.value})")
            self._advance_event.set()

    def stop(self):
        """Request session stop."""
        with self._lock:
            self._stop_event.set()
            self._advance_event.set()

    def _save_manifest(self):
        if not self.session_dir:
            return
        path = self.session_dir / "manifest.json"
        tmp = self.session_dir / "manifest.json.tmp"
        tmp.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _sync_child_study(self):
        if not self.session_config or self.session_config.no_sync or not self.session_dir:
            return
        ensure_child_study_dirs()
        rel_dir = str(self.session_dir.relative_to(ROOT))
        cfg = self.session_config

        session_record = {
            "session_id": self.session_id,
            "created_at": self.manifest["created_at"],
            "speaker_id": cfg.speaker_id,
            "speaker_label": cfg.speaker,
            "purpose": cfg.split,
            "device": self.audio_device,
            "distance_m": cfg.distance_m,
            "condition": cfg.condition,
            "directory": rel_dir,
            "expected_phrase": cfg.phrase,
            "label": cfg.label,
            "takes_planned": cfg.takes_planned,
            "takes_captured": len(self.clips),
            "status": self.manifest["status"],
            "reviewer": "pending",
            "notes": f"Thu local qua dashboard ({cfg.split})",
        }

        label_entries = []
        for idx, clip in enumerate(self.clips, start=1):
            sample_id = f"{cfg.speaker_id}-{self.session_id}-t{idx:02d}"
            wav_path = rel_dir + "/" + clip.file_name
            label_entry = create_label_entry(
                sample_id=sample_id,
                source=wav_path,
                source_sha256=clip.sha256,
                speaker_id=cfg.speaker_id,
                speaker_label=cfg.speaker,
                session_id=self.session_id,
                split=cfg.split,
                label=cfg.label,
                transcript_human=cfg.phrase,
                expected_events=cfg.expected_events,
                distance_m=cfg.distance_m,
                condition=cfg.condition,
                speaker_confirmed=False,
                review_status="captured_pending_review",
                review_note="Mới thu qua dashboard, chờ nghe lại và duyệt",
            )
            label_entries.append(label_entry)

        save_session_and_labels(session_record, label_entries)

    def _run_session_worker(self):
        try:
            # Loop through takes
            for take in range(1, self.session_config.takes_planned + 1):
                if self._stop_event.is_set():
                    break

                with self._lock:
                    self.current_take = take
                    self.state = RecordingState.WAITING_USER
                    self._advance_event.clear()

                # Wait for user trigger if manual advance
                if self.session_config.manual_advance:
                    while not self._advance_event.is_set() and not self._stop_event.is_set():
                        time.sleep(0.1)
                    if self._stop_event.is_set():
                        break

                # R05: Open capture BEFORE playing cue so DC warmup is completed before cue
                if not self.session_config.mock:
                    take_capture_cm = AlsaCapture(self.audio_device, warmup_seconds=0.5)
                else:
                    take_capture_cm = contextlib.nullcontext()

                with take_capture_cm as capture:
                    # Play Cue while draining capture
                    with self._lock:
                        self.state = RecordingState.CUE
                    self._play_cue_tone(capture=capture)

                    if self._stop_event.is_set():
                        break

                    # Record exact 5.0 seconds
                    with self._lock:
                        self.state = RecordingState.RECORDING
                    pcm = self._record_pcm(5.0, capture=capture)

                # Process & Save
                with self._lock:
                    self.state = RecordingState.PROCESSING

                name = f"take-{take:02d}.wav"
                wav_path = self.session_dir / name
                self._write_wav(wav_path, pcm)

                stats = pcm_stats(pcm)
                sha256 = hashlib.sha256(wav_path.read_bytes()).hexdigest()
                clip = TakeClip(
                    take_number=take,
                    file_name=name,
                    recorded_at=datetime.now().astimezone().isoformat(),
                    peak=stats["peak"],
                    rms=stats["rms"],
                    clipped_percent=stats["clipped_percent"],
                    sha256=sha256,
                )
                self.clips.append(clip)
                self.manifest["clips"].append({
                    "file": name,
                    "recorded_at": clip.recorded_at,
                    "stats": stats,
                    "sha256": sha256,
                })
                self._save_manifest()

                if stats["clipped_percent"] > 1.0:
                    raise AudioError(
                        f"Audio clipping {stats['clipped_percent']:.2f}% (> 1%) ở lượt {take}. "
                        f"Dừng phiên để kiểm tra gain đầu vào. Không tự ý thay đổi gain!"
                    )

            # Finalize
            with self._lock:
                if self._stop_event.is_set():
                    self.manifest["status"] = "interrupted"
                    self.state = RecordingState.INTERRUPTED
                else:
                    self.manifest["status"] = "captured_pending_review"
                    self.state = RecordingState.COMPLETED
                self._save_manifest()

            try:
                self._sync_child_study()
            except Exception as exc:
                self.manifest["status"] = "sync_failed"
                self.manifest["sync_error"] = str(exc)
                self._save_manifest()

        except Exception as exc:
            with self._lock:
                self.state = RecordingState.FAILED
                self.error_message = str(exc)
                self.manifest["status"] = "interrupted"
                self.manifest["error"] = str(exc)
                self._save_manifest()
            try:
                self._sync_child_study()
            except Exception:
                pass
        finally:
            if self._audio_res_lock:
                self._audio_res_lock.release()
                self._audio_res_lock = None

    def _play_cue_tone(self, capture=None):
        if self.session_config.mock:
            time.sleep(0.1)
            return

        with tempfile.TemporaryDirectory(prefix="smart-hub-cue-") as cue_directory:
            cue_path = Path(cue_directory) / "cue.wav"
            count = round(RATE * 0.12)
            values = array(
                "h",
                (
                    round(5000 * math.sin(2 * math.pi * 880 * i / RATE) * min(1, i / 160, (count - 1 - i) / 160))
                    for i in range(count)
                ),
            )
            if sys.byteorder != "little":
                values.byteswap()
            self._write_wav(cue_path, values.tobytes())

            try:
                subprocess.run(
                    ["aplay", "-q", "-D", self.playback_device, str(cue_path)],
                    check=False,
                    timeout=3.0,
                )
            except Exception:
                pass

        if capture is not None:
            time.sleep(0.05)
            if hasattr(capture, "drain"):
                capture.drain()

    def _record_pcm(self, seconds: float, capture=None) -> bytes:
        if self.session_config.mock:
            time.sleep(0.3)
            # Return synthetic 16-bit mono 16kHz audio with moderate RMS and zero clipping
            count = round(RATE * seconds)
            values = array("h", (round(1200 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(count)))
            if sys.byteorder != "little":
                values.byteswap()
            return values.tobytes()

        if capture is not None:
            if hasattr(capture, "drain"):
                capture.drain()
            frames_needed = math.ceil(seconds * 50)
            return b"".join(capture.read_frame() for _ in range(frames_needed))

        with AlsaCapture(self.audio_device) as cap:
            # Drop trailing cue echo (5 frames = 0.1s)
            for _ in range(5):
                cap.read_frame()
            # Capture exact seconds
            frames_needed = math.ceil(seconds * 50)
            return b"".join(cap.read_frame() for _ in range(frames_needed))

    @staticmethod
    def _write_wav(path: Path, pcm: bytes):
        with path.open("xb") as file, wave.open(file, "wb") as wav:
            wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
