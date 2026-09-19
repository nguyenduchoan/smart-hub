"""Model, artifact, and candidate registry for Wake Word Lab."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from ..config import ROOT

CANDIDATES_DIR = ROOT / ".local" / "dashboard" / "wake-candidates"
DEFAULT_DB_PATH = ROOT / ".local" / "dashboard" / "app.sqlite"


class WakeEngine(str, Enum):
    SHERPA_ONNX_STT = "sherpa_onnx_stt"
    DTW = "dtw"
    EFFICIENTWORD_NET = "efficientword_net"
    MOCK = "mock"
    CUSTOM = "custom"


@dataclass
class ModelArtifact:
    id: str
    engine: WakeEngine
    name: str
    files: List[str]
    total_size_bytes: int
    content_hash: str
    phrase: str = "Maika ơi"
    language: str = "vi"
    source: str = "local"
    license: str = "Apache-2.0"
    compatibility_status: str = "verified"     # verified, untested, incompatible
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["engine"] = self.engine.value
        return data


@dataclass
class WakeCandidate:
    id: str
    name: str
    model_id: str
    engine: WakeEngine
    profile: str                               # 'standard', 'sensitive', 'custom'
    threshold: float = 0.5
    alias_config: Dict[str, Any] = field(default_factory=dict)
    reference_sample_ids: List[str] = field(default_factory=list)
    config_hash: str = ""
    is_baseline: bool = False
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def __post_init__(self):
        if not self.config_hash:
            h = hashlib.sha256(
                f"{self.id}:{self.engine.value}:{self.profile}:{self.threshold}:{json.dumps(self.alias_config, sort_keys=True)}:{sorted(self.reference_sample_ids)}".encode(
                    "utf-8"
                )
            ).hexdigest()
            self.config_hash = h[:16]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["engine"] = self.engine.value
        return data


class WakeRegistry:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._ensure_tables()
        self._seed_baseline_candidates()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self):
        with self._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS wake_artifacts (
                id TEXT PRIMARY KEY,
                engine TEXT NOT NULL,
                name TEXT NOT NULL,
                files TEXT NOT NULL, -- JSON list
                total_size_bytes INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                phrase TEXT NOT NULL,
                language TEXT NOT NULL,
                source TEXT NOT NULL,
                license TEXT NOT NULL,
                compatibility_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wake_candidates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                model_id TEXT NOT NULL,
                engine TEXT NOT NULL,
                profile TEXT NOT NULL,
                threshold REAL NOT NULL,
                alias_config TEXT NOT NULL, -- JSON dict
                reference_sample_ids TEXT NOT NULL, -- JSON list
                config_hash TEXT NOT NULL,
                is_baseline INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wake_evaluations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                candidate_ids TEXT NOT NULL, -- JSON list
                split TEXT NOT NULL,
                mode TEXT NOT NULL,          -- 'official' or 'all'
                dataset_snapshot_hash TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                results_json TEXT NOT NULL,
                report_markdown TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                created_at TEXT NOT NULL
            );
            """)

    def _seed_baseline_candidates(self):
        """Seed baseline standard and sensitive Vietnamese STT candidates."""
        baselines = [
            WakeCandidate(
                id="cand_stt_standard",
                name="STT Tiếng Việt - Standard (Baseline)",
                model_id="artifact_stt_sherpa_vi",
                engine=WakeEngine.SHERPA_ONNX_STT,
                profile="standard",
                threshold=0.5,
                alias_config={"exact_phrase_only": True},
                is_baseline=True,
                notes="Baseline chính thức; yêu cầu phát âm chuẩn xác 'Maika ơi'.",
            ),
            WakeCandidate(
                id="cand_stt_sensitive",
                name="STT Tiếng Việt - Sensitive (Trẻ em)",
                model_id="artifact_stt_sherpa_vi",
                engine=WakeEngine.SHERPA_ONNX_STT,
                profile="sensitive",
                threshold=0.35,
                alias_config={"aliases": ["mai ca ơi", "mai ơi", "mẹ ơi", "maika"]},
                is_baseline=True,
                notes="Profile nhạy hơn; hỗ trợ các biến thể phát âm gần của trẻ em.",
            ),
            WakeCandidate(
                id="cand_dtw_baseline",
                name="DTW Audio Embedding (Baseline)",
                model_id="artifact_dtw_baseline",
                engine=WakeEngine.DTW,
                profile="standard",
                threshold=0.6,
                is_baseline=True,
                notes="Baseline so sánh dựa trên trích xuất đặc trưng âm học.",
            ),
        ]
        for b in baselines:
            if not self.get_candidate(b.id):
                self.save_candidate(b)

    def save_candidate(self, cand: WakeCandidate):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wake_candidates
                (id, name, model_id, engine, profile, threshold, alias_config, reference_sample_ids, config_hash, is_baseline, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cand.id, cand.name, cand.model_id, cand.engine.value, cand.profile,
                    cand.threshold, json.dumps(cand.alias_config, ensure_ascii=False),
                    json.dumps(cand.reference_sample_ids, ensure_ascii=False),
                    cand.config_hash, 1 if cand.is_baseline else 0, cand.notes, cand.created_at,
                ),
            )

    def get_candidate(self, candidate_id: str) -> Optional[WakeCandidate]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM wake_candidates WHERE id = ?", (candidate_id,)).fetchone()
            if not row:
                return None
            return WakeCandidate(
                id=row["id"],
                name=row["name"],
                model_id=row["model_id"],
                engine=WakeEngine(row["engine"]),
                profile=row["profile"],
                threshold=row["threshold"],
                alias_config=json.loads(row["alias_config"]),
                reference_sample_ids=json.loads(row["reference_sample_ids"]),
                config_hash=row["config_hash"],
                is_baseline=bool(row["is_baseline"]),
                notes=row["notes"],
                created_at=row["created_at"],
            )

    def list_candidates(self) -> List[WakeCandidate]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM wake_candidates ORDER BY is_baseline DESC, created_at ASC").fetchall()
            return [
                WakeCandidate(
                    id=r["id"],
                    name=r["name"],
                    model_id=r["model_id"],
                    engine=WakeEngine(r["engine"]),
                    profile=r["profile"],
                    threshold=r["threshold"],
                    alias_config=json.loads(r["alias_config"]),
                    reference_sample_ids=json.loads(r["reference_sample_ids"]),
                    config_hash=r["config_hash"],
                    is_baseline=bool(r["is_baseline"]),
                    notes=r["notes"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def save_evaluation(self, eval_id: str, name: str, candidate_ids: List[str], split: str, mode: str,
                        snapshot_hash: str, sample_count: int, results_json: str, report_md: str):
        with self._get_connection() as conn:
            now = datetime.now().astimezone().isoformat()
            conn.execute(
                """
                INSERT OR REPLACE INTO wake_evaluations
                (id, name, candidate_ids, split, mode, dataset_snapshot_hash, sample_count, results_json, report_markdown, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)
                """,
                (eval_id, name, json.dumps(candidate_ids), split, mode, snapshot_hash, sample_count, results_json, report_md, now),
            )

    def get_evaluation(self, eval_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM wake_evaluations WHERE id = ?", (eval_id,)).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "name": row["name"],
                "candidate_ids": json.loads(row["candidate_ids"]),
                "split": row["split"],
                "mode": row["mode"],
                "snapshot_hash": row["dataset_snapshot_hash"],
                "sample_count": row["sample_count"],
                "results": json.loads(row["results_json"]),
                "report_markdown": row["report_markdown"],
                "status": row["status"],
                "created_at": row["created_at"],
            }

    def list_evaluations(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT id, name, candidate_ids, split, mode, sample_count, status, created_at FROM wake_evaluations ORDER BY created_at DESC").fetchall()
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "candidate_ids": json.loads(r["candidate_ids"]),
                    "split": r["split"],
                    "mode": r["mode"],
                    "sample_count": r["sample_count"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
