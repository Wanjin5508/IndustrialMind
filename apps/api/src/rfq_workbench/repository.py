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
            if version not in (0, 1):
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
                PRAGMA user_version=1;
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
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            body = self._get(conn, run_id, project)
            if body["version"] != expected_version:
                raise DomainError(
                    "VERSION_CONFLICT", "Task changed. Refresh before retrying this action.", 409
                )
            detail = change(body)
            body["version"] += 1
            conn.execute(
                "UPDATE runs SET version=?,body=? WHERE id=?",
                (body["version"], json.dumps(body), run_id),
            )
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?)",
                (run_id, sequence, utc_now(), actor, action, json.dumps(detail)),
            )
        return self.get(run_id, project)
