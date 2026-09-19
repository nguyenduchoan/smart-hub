"""SQLite storage for gateways, appliances, code catalogs, code revisions, observations, and command ledger."""
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from ..config import ROOT
from .base import (
    Appliance,
    ApplianceCategory,
    CodeRevision,
    CodeSet,
    CommandLedgerEntry,
    CommandState,
    GatewayCheckResult,
    GatewayInfo,
    GatewayStatus,
    Observation,
    ObservationOutcome,
)

DEFAULT_DB_PATH = ROOT / ".local" / "dashboard" / "app.sqlite"


class DeviceStorage:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._ensure_db_dir()
        self._init_db()

    def _ensure_db_dir(self):
        old_umask = os.umask(0o077)
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        finally:
            os.umask(old_umask)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS gateways (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model_name TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                mac TEXT NOT NULL UNIQUE,
                devtype INTEGER NOT NULL,
                is_locked INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL DEFAULT '',
                room TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'online',
                fwversion INTEGER,
                last_checked_at TEXT
            );

            CREATE TABLE IF NOT EXISTS code_sets (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                brand TEXT NOT NULL,
                models TEXT NOT NULL, -- JSON list
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                license TEXT NOT NULL,
                encoding TEXT NOT NULL DEFAULT 'broadlink_base64',
                hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                codes TEXT NOT NULL -- JSON dict button_key -> base64
            );
            CREATE INDEX IF NOT EXISTS idx_code_sets_category_brand ON code_sets(category, brand);

            CREATE TABLE IF NOT EXISTS appliances (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                room TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                brand TEXT NOT NULL,
                model TEXT NOT NULL,
                gateway_id TEXT NOT NULL REFERENCES gateways(id) ON DELETE CASCADE,
                code_set_id TEXT REFERENCES code_sets(id) ON DELETE SET NULL,
                mapping_revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS code_revisions (
                id TEXT PRIMARY KEY,
                code_set_id TEXT REFERENCES code_sets(id) ON DELETE SET NULL,
                appliance_id TEXT REFERENCES appliances(id) ON DELETE CASCADE,
                button_key TEXT NOT NULL,
                button_name TEXT NOT NULL,
                payload_base64 TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                source_type TEXT NOT NULL, -- 'catalog' or 'learned'
                revision_number INTEGER NOT NULL DEFAULT 1,
                is_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_code_revisions_appliance_btn ON code_revisions(appliance_id, button_key);

            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                appliance_id TEXT NOT NULL REFERENCES appliances(id) ON DELETE CASCADE,
                code_revision_id TEXT NOT NULL REFERENCES code_revisions(id) ON DELETE CASCADE,
                button_key TEXT NOT NULL,
                outcome TEXT NOT NULL,
                user_notes TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS command_ledger (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                gateway_id TEXT NOT NULL,
                appliance_id TEXT NOT NULL,
                button_key TEXT NOT NULL,
                code_revision_id TEXT NOT NULL,
                state TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                completed_at TEXT,
                error_message TEXT,
                raw_ack TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_req ON command_ledger(request_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_state ON command_ledger(state);
            """)

    def recover_interrupted_commands(self):
        """Mark any command left in 'dispatching' or 'prepared' as 'unknown' after restart."""
        with self._get_connection() as conn:
            now = datetime.now().astimezone().isoformat()
            conn.execute(
                """
                UPDATE command_ledger
                SET state = 'unknown',
                    completed_at = ?,
                    error_message = 'Interrupted by server restart before completion'
                WHERE state IN ('prepared', 'dispatching')
                """,
                (now,),
            )

    # --- Gateways ---

    def save_gateway(self, gw: GatewayInfo):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO gateways (id, provider, model_name, ip_address, mac, devtype, is_locked, name, room, status, fwversion, last_checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    model_name = excluded.model_name,
                    ip_address = excluded.ip_address,
                    devtype = excluded.devtype,
                    is_locked = excluded.is_locked,
                    name = CASE WHEN excluded.name != '' THEN excluded.name ELSE gateways.name END,
                    room = CASE WHEN excluded.room != '' THEN excluded.room ELSE gateways.room END,
                    status = excluded.status,
                    fwversion = coalesce(excluded.fwversion, gateways.fwversion),
                    last_checked_at = excluded.last_checked_at
                """,
                (
                    gw.id, gw.provider, gw.model_name, gw.ip_address, gw.mac, gw.devtype,
                    1 if gw.is_locked else 0, gw.name, gw.room, gw.status.value,
                    gw.fwversion, gw.last_checked_at or datetime.now().astimezone().isoformat(),
                ),
            )

    def get_gateway(self, gateway_id: str) -> Optional[GatewayInfo]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM gateways WHERE id = ?", (gateway_id,)).fetchone()
            if not row:
                return None
            return self._row_to_gateway(row)

    def get_gateway_by_mac(self, mac: str) -> Optional[GatewayInfo]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM gateways WHERE mac = ?", (mac,)).fetchone()
            if not row:
                return None
            return self._row_to_gateway(row)

    def list_gateways(self) -> List[GatewayInfo]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM gateways ORDER BY name ASC, ip_address ASC").fetchall()
            return [self._row_to_gateway(r) for r in rows]

    def update_gateway_status(self, gateway_id: str, status: GatewayStatus, is_locked: Optional[bool] = None, fwversion: Optional[int] = None):
        with self._get_connection() as conn:
            now = datetime.now().astimezone().isoformat()
            if is_locked is not None:
                conn.execute(
                    "UPDATE gateways SET status = ?, is_locked = ?, last_checked_at = ? WHERE id = ?",
                    (status.value, 1 if is_locked else 0, now, gateway_id),
                )
            else:
                conn.execute(
                    "UPDATE gateways SET status = ?, last_checked_at = ? WHERE id = ?",
                    (status.value, now, gateway_id),
                )

    def _row_to_gateway(self, row: sqlite3.Row) -> GatewayInfo:
        return GatewayInfo(
            id=row["id"],
            provider=row["provider"],
            model_name=row["model_name"],
            ip_address=row["ip_address"],
            mac=row["mac"],
            devtype=row["devtype"],
            is_locked=bool(row["is_locked"]),
            name=row["name"],
            room=row["room"],
            status=GatewayStatus(row["status"]),
            fwversion=row["fwversion"],
            last_checked_at=row["last_checked_at"],
        )

    # --- Code Sets ---

    def save_code_set(self, cs: CodeSet):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO code_sets
                (id, category, brand, models, source_name, source_url, source_revision, license, encoding, hash, created_at, codes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cs.id, cs.category.value, cs.brand, json.dumps(cs.models, ensure_ascii=False),
                    cs.source_name, cs.source_url, cs.source_revision, cs.license,
                    cs.encoding, cs.hash, cs.created_at, json.dumps(cs.codes, ensure_ascii=False),
                ),
            )

    def get_code_set(self, code_set_id: str) -> Optional[CodeSet]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM code_sets WHERE id = ?", (code_set_id,)).fetchone()
            if not row:
                return None
            return self._row_to_code_set(row)

    def list_code_sets(self, category: Optional[str] = None, brand: Optional[str] = None) -> List[CodeSet]:
        with self._get_connection() as conn:
            query = "SELECT * FROM code_sets WHERE 1=1"
            params = []
            if category:
                query += " AND category = ?"
                params.append(category)
            if brand:
                query += " AND lower(brand) = lower(?)"
                params.append(brand)
            query += " ORDER BY brand ASC, id ASC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_code_set(r) for r in rows]

    def list_brands(self, category: Optional[str] = None) -> List[str]:
        with self._get_connection() as conn:
            if category:
                rows = conn.execute("SELECT DISTINCT brand FROM code_sets WHERE category = ? ORDER BY brand ASC", (category,)).fetchall()
            else:
                rows = conn.execute("SELECT DISTINCT brand FROM code_sets ORDER BY brand ASC").fetchall()
            return [r["brand"] for r in rows]

    def _row_to_code_set(self, row: sqlite3.Row) -> CodeSet:
        return CodeSet(
            id=row["id"],
            category=ApplianceCategory(row["category"]),
            brand=row["brand"],
            models=json.loads(row["models"]),
            source_name=row["source_name"],
            source_url=row["source_url"],
            source_revision=row["source_revision"],
            license=row["license"],
            encoding=row["encoding"],
            hash=row["hash"],
            created_at=row["created_at"],
            codes=json.loads(row["codes"]),
        )

    # --- Appliances ---

    def save_appliance(self, app: Appliance):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO appliances
                (id, name, room, category, brand, model, gateway_id, code_set_id, mapping_revision, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    app.id, app.name, app.room, app.category.value, app.brand, app.model,
                    app.gateway_id, app.code_set_id, app.mapping_revision, app.created_at, app.updated_at,
                ),
            )

    def get_appliance(self, app_id: str) -> Optional[Appliance]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM appliances WHERE id = ?", (app_id,)).fetchone()
            if not row:
                return None
            return self._row_to_appliance(row)

    def list_appliances(self, gateway_id: Optional[str] = None) -> List[Appliance]:
        with self._get_connection() as conn:
            if gateway_id:
                rows = conn.execute("SELECT * FROM appliances WHERE gateway_id = ? ORDER BY name ASC", (gateway_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM appliances ORDER BY room ASC, name ASC").fetchall()
            return [self._row_to_appliance(r) for r in rows]

    def delete_appliance(self, app_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM appliances WHERE id = ?", (app_id,))
            return cur.rowcount > 0

    def _row_to_appliance(self, row: sqlite3.Row) -> Appliance:
        return Appliance(
            id=row["id"],
            name=row["name"],
            room=row["room"],
            category=ApplianceCategory(row["category"]),
            brand=row["brand"],
            model=row["model"],
            gateway_id=row["gateway_id"],
            code_set_id=row["code_set_id"],
            mapping_revision=row["mapping_revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # --- Code Revisions ---

    def save_code_revision(self, rev: CodeRevision):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO code_revisions
                (id, code_set_id, appliance_id, button_key, button_name, payload_base64, payload_hash, source_type, revision_number, is_verified, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rev.id, rev.code_set_id, rev.appliance_id, rev.button_key, rev.button_name,
                    rev.payload_base64, rev.payload_hash, rev.source_type, rev.revision_number,
                    1 if rev.is_verified else 0, rev.created_at,
                ),
            )

    def get_code_revision(self, rev_id: str) -> Optional[CodeRevision]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM code_revisions WHERE id = ?", (rev_id,)).fetchone()
            if not row:
                return None
            return self._row_to_code_revision(row)

    def list_code_revisions(self, appliance_id: Optional[str] = None, code_set_id: Optional[str] = None) -> List[CodeRevision]:
        with self._get_connection() as conn:
            query = "SELECT * FROM code_revisions WHERE 1=1"
            params = []
            if appliance_id:
                query += " AND appliance_id = ?"
                params.append(appliance_id)
            if code_set_id:
                query += " AND code_set_id = ?"
                params.append(code_set_id)
            query += " ORDER BY button_key ASC, revision_number DESC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_code_revision(r) for r in rows]

    def get_active_code_revision(self, appliance_id: str, button_key: str) -> Optional[CodeRevision]:
        """Get the active code revision for a button. Prioritizes verified revisions (R13)."""
        with self._get_connection() as conn:
            # 1. First look for verified revisions (active binding)
            row = conn.execute(
                """
                SELECT * FROM code_revisions
                WHERE appliance_id = ? AND button_key = ? AND is_verified = 1
                ORDER BY revision_number DESC, created_at DESC LIMIT 1
                """,
                (appliance_id, button_key),
            ).fetchone()
            if row:
                return self._row_to_code_revision(row)

            # 2. If no verified revision exists yet, fallback to latest initial revision
            row_unverified = conn.execute(
                """
                SELECT * FROM code_revisions
                WHERE appliance_id = ? AND button_key = ?
                ORDER BY revision_number DESC, created_at DESC LIMIT 1
                """,
                (appliance_id, button_key),
            ).fetchone()
            if row_unverified:
                return self._row_to_code_revision(row_unverified)

            return None

    def verify_code_revision(self, rev_id: str, is_verified: bool = True):
        with self._get_connection() as conn:
            conn.execute("UPDATE code_revisions SET is_verified = ? WHERE id = ?", (1 if is_verified else 0, rev_id))

    def _row_to_code_revision(self, row: sqlite3.Row) -> CodeRevision:
        return CodeRevision(
            id=row["id"],
            code_set_id=row["code_set_id"],
            appliance_id=row["appliance_id"],
            button_key=row["button_key"],
            button_name=row["button_name"],
            payload_base64=row["payload_base64"],
            payload_hash=row["payload_hash"],
            source_type=row["source_type"],
            revision_number=row["revision_number"],
            is_verified=bool(row["is_verified"]),
            created_at=row["created_at"],
        )

    # --- Observations ---

    def record_observation(self, obs: Observation):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO observations (id, appliance_id, code_revision_id, button_key, outcome, user_notes, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (obs.id, obs.appliance_id, obs.code_revision_id, obs.button_key, obs.outcome.value, obs.user_notes, obs.recorded_at),
            )
            # If accurate, mark revision as verified
            if obs.outcome == ObservationOutcome.ACCURATE:
                conn.execute("UPDATE code_revisions SET is_verified = 1 WHERE id = ?", (obs.code_revision_id,))

    def list_observations(self, appliance_id: str) -> List[Observation]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM observations WHERE appliance_id = ? ORDER BY recorded_at DESC",
                (appliance_id,),
            ).fetchall()
            return [
                Observation(
                    id=r["id"],
                    appliance_id=r["appliance_id"],
                    code_revision_id=r["code_revision_id"],
                    button_key=r["button_key"],
                    outcome=ObservationOutcome(r["outcome"]),
                    user_notes=r["user_notes"],
                    recorded_at=r["recorded_at"],
                )
                for r in rows
            ]

    # --- Command Ledger ---

    def get_ledger_entry(self, request_id: str) -> Optional[CommandLedgerEntry]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM command_ledger WHERE request_id = ?", (request_id,)).fetchone()
            if not row:
                return None
            return self._row_to_ledger_entry(row)

    def prepare_command(self, request_id: str, gateway_id: str, appliance_id: str, button_key: str, code_revision_id: str) -> CommandLedgerEntry:
        now = datetime.now().astimezone().isoformat()
        entry_id = f"cmd_{uuid.uuid4().hex[:12]}"
        entry = CommandLedgerEntry(
            id=entry_id,
            request_id=request_id,
            gateway_id=gateway_id,
            appliance_id=appliance_id,
            button_key=button_key,
            code_revision_id=code_revision_id,
            state=CommandState.PREPARED,
            sent_at=now,
        )
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO command_ledger (id, request_id, gateway_id, appliance_id, button_key, code_revision_id, state, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entry.id, entry.request_id, entry.gateway_id, entry.appliance_id, entry.button_key, entry.code_revision_id, entry.state.value, entry.sent_at),
            )
        return entry

    def update_command_state(self, request_id: str, state: CommandState, error_message: Optional[str] = None, raw_ack: Optional[str] = None):
        now = datetime.now().astimezone().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE command_ledger
                SET state = ?, completed_at = ?, error_message = ?, raw_ack = ?
                WHERE request_id = ?
                """,
                (state.value, now, error_message, raw_ack, request_id),
            )

    def _row_to_ledger_entry(self, row: sqlite3.Row) -> CommandLedgerEntry:
        return CommandLedgerEntry(
            id=row["id"],
            request_id=row["request_id"],
            gateway_id=row["gateway_id"],
            appliance_id=row["appliance_id"],
            button_key=row["button_key"],
            code_revision_id=row["code_revision_id"],
            state=CommandState(row["state"]),
            sent_at=row["sent_at"],
            completed_at=row["completed_at"],
            error_message=row["error_message"],
            raw_ack=row["raw_ack"],
        )
