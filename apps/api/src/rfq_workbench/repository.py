"""SQLite transactions keep task snapshots and audit events consistent."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from rfq_workbench.errors import DomainError


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Repository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError("Unsupported database schema version")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    version INTEGER NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(id), sequence INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
                    detail TEXT NOT NULL, PRIMARY KEY (run_id, sequence)
                );
            """)
            if version < 2:
                conn.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS source_snapshots (
                        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                        system TEXT NOT NULL, entity TEXT NOT NULL, external_id TEXT NOT NULL,
                        plant TEXT NOT NULL, source_version TEXT NOT NULL, as_of TEXT NOT NULL,
                        fetched_at TEXT NOT NULL, ttl_seconds INTEGER NOT NULL,
                        payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                        adapter_version TEXT NOT NULL, mapping_version TEXT NOT NULL,
                        unknown_fields TEXT NOT NULL DEFAULT '[]'
                    );
                    CREATE INDEX IF NOT EXISTS ix_snapshots_run ON source_snapshots(run_id);
                    CREATE TABLE IF NOT EXISTS outbox (
                        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                        target TEXT NOT NULL, operation TEXT NOT NULL, business_key TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL, approved_quote_hash TEXT NOT NULL,
                        base_snapshot_ids TEXT NOT NULL, mapping_version TEXT NOT NULL,
                        external_id TEXT, expected_target_version TEXT, state TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                        next_attempt_at TEXT, lease_until TEXT, lease_token TEXT,
                        last_error_class TEXT, last_error_code TEXT, receipt TEXT,
                        requested_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_active ON outbox(run_id, target)
                        WHERE state NOT IN ('VERIFIED','REJECTED','QUARANTINED','SUPERSEDED');
                    CREATE TABLE IF NOT EXISTS outbox_attempts (
                        intent_id TEXT NOT NULL REFERENCES outbox(id), attempt INTEGER NOT NULL,
                        started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT,
                        error_class TEXT, error_code TEXT, http_status INTEGER,
                        request_sha256 TEXT, response_sha256 TEXT, elapsed_ms INTEGER,
                        PRIMARY KEY (intent_id, attempt)
                    );
                    CREATE TABLE IF NOT EXISTS reconciliations (
                        id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES outbox(id),
                        checked_at TEXT NOT NULL, status TEXT NOT NULL, diffs TEXT NOT NULL,
                        target_version TEXT
                    );
                    CREATE TABLE IF NOT EXISTS idempotency_records (
                        scope TEXT NOT NULL, key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                        status INTEGER, response TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                        PRIMARY KEY (scope, key)
                    );
                    PRAGMA user_version=2;
                    COMMIT;
                """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _get(self, conn: sqlite3.Connection, run_id: str, project: str) -> dict:
        row = conn.execute(
            "SELECT body FROM runs WHERE id=? AND project_id=?", (run_id, project)
        ).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", "Task not found in this project.", 404)
        return json.loads(row["body"])

    def get(self, run_id: str, project: str = "PM-DEMO") -> dict:
        with self.connect() as conn:
            body = self._get(conn, run_id, project)
            body["events"] = [
                dict(row) | {"detail": json.loads(row["detail"])}
                for row in conn.execute(
                    "SELECT * FROM events WHERE run_id=? ORDER BY sequence", (run_id,)
                )
            ]
            return body

    def list(self, project: str = "PM-DEMO") -> list[dict]:
        with self.connect() as conn:
            return [
                json.loads(row["body"])
                for row in conn.execute(
                    "SELECT body FROM runs WHERE project_id=? ORDER BY created_at DESC", (project,)
                )
            ]

    def create(self, body: dict, actor: str) -> dict:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?)",
                (body["id"], body["project_id"], 1, body["created_at"], json.dumps(body)),
            )
            conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?)",
                (body["id"], 1, utc_now(), actor, "created", json.dumps({"mode": body["mode"]})),
            )
        return self.get(body["id"], body["project_id"])

    def update(
        self,
        run_id: str,
        expected_version: int,
        actor: str,
        action: str,
        change: Callable[[dict], dict],
        project: str = "PM-DEMO",
    ) -> dict:
        return self.update_tx(
            run_id, expected_version, actor, action, lambda body, _conn: change(body), project
        )

    def append_event(
        self, conn: sqlite3.Connection, run_id: str, actor: str, action: str, detail: dict
    ) -> None:
        sequence = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?)",
            (run_id, sequence, utc_now(), actor, action, json.dumps(detail)),
        )

    def update_tx(
        self,
        run_id: str,
        expected_version: int,
        actor: str,
        action: str,
        change: Callable[[dict, sqlite3.Connection], dict],
        project: str = "PM-DEMO",
    ) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            body = self._get(conn, run_id, project)
            if body["version"] != expected_version:
                raise DomainError(
                    "VERSION_CONFLICT", "Task changed. Refresh before retrying this action.", 409
                )
            detail = change(body, conn)
            body["version"] += 1
            conn.execute(
                "UPDATE runs SET version=?,body=? WHERE id=?",
                (body["version"], json.dumps(body), run_id),
            )
            self.append_event(conn, run_id, actor, action, detail)
        return self.get(run_id, project)
