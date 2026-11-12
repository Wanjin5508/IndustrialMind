"""Agent state and pending tool decisions survive process interruption."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from rfq_workbench.errors import DomainError


class AgentStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )

    def create(self, body: dict) -> dict:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO agent_runs VALUES (?,?)", (body["id"], json.dumps(body)))
        return body

    @staticmethod
    def read(conn, run_id: str, actor: str) -> dict:
        row = conn.execute("SELECT body FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row:
            body = json.loads(row[0])
            if body["actor"] == actor:
                return body
        raise DomainError("AGENT_NOT_FOUND", "找不到该 Agent 任务。", 404)

    def get(self, run_id: str, actor: str) -> dict:
        with closing(sqlite3.connect(self.path)) as conn:
            return self.read(conn, run_id, actor)

    def update(self, run_id: str, actor: str, change) -> dict:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            body = self.read(conn, run_id, actor)
            change(body)
            conn.execute("UPDATE agent_runs SET body=? WHERE id=?", (json.dumps(body), run_id))
            return body
