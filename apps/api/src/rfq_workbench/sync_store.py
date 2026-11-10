"""Transactional outbox persistence. Remote adapters are never called here."""

from __future__ import annotations

import json
from uuid import uuid4

from rfq_workbench.canonical import canonical_json
from rfq_workbench.errors import DomainError
from rfq_workbench.integrations.contracts import OutboundIntent, ReconciliationResult

JSON_COLUMNS = {"payload", "base_snapshot_ids", "receipt"}


class SyncStore:
    def __init__(self, repo, clock):
        self.repo, self.clock = repo, clock

    @staticmethod
    def decode(row) -> OutboundIntent:
        value = dict(row)
        for key in JSON_COLUMNS:
            if value[key] is not None:
                value[key] = json.loads(value[key])
        return OutboundIntent.model_validate(value)

    def get(self, identifier: str, conn=None) -> OutboundIntent:
        if conn is None:
            with self.repo.connect() as connection:
                return self.get(identifier, connection)
        row = conn.execute("SELECT * FROM outbox WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise DomainError("NOT_FOUND", "同步意图不存在。", 404)
        return self.decode(row)

    def save(self, conn, intent: OutboundIntent, *, insert: bool = False) -> None:
        values = intent.model_dump(mode="json")
        for key in JSON_COLUMNS:
            if values[key] is not None:
                values[key] = canonical_json(values[key])
        if insert:
            columns = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            conn.execute(
                f"INSERT INTO outbox({columns}) VALUES({placeholders})", tuple(values.values())
            )
        else:
            identifier = values.pop("id")
            assignments = ",".join(key + "=?" for key in values)
            conn.execute(
                f"UPDATE outbox SET {assignments} WHERE id=?", (*values.values(), identifier)
            )

    def list(self, run_id: str | None = None) -> list[OutboundIntent]:
        with self.repo.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM outbox"
                + (" WHERE run_id=?" if run_id else "")
                + " ORDER BY created_at,rowid",
                (run_id,) if run_id else (),
            ).fetchall()
        return [self.decode(row) for row in rows]

    def event(self, conn, intent, action: str, triggered_by: str, **detail) -> None:
        self.repo.append_event(
            conn,
            intent.run_id,
            "system-dispatcher",
            action,
            {"intent_id": intent.id, "state": intent.state, "triggered_by": triggered_by, **detail},
        )

    def reconcile(
        self, intent: OutboundIntent, result: ReconciliationResult, triggered_by: str
    ) -> OutboundIntent:
        with self.repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.get(intent.id, conn)
            if current.state != intent.state or current.updated_at != intent.updated_at:
                return current
            conn.execute(
                "INSERT INTO reconciliations VALUES(?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    intent.id,
                    result.checked_at.isoformat(),
                    result.status,
                    canonical_json([d.model_dump() for d in result.diffs]),
                    result.target_version,
                ),
            )
            if current.state == "VERIFIED":
                if result.status != "MATCH":
                    self.event(
                        conn,
                        current,
                        "target_drift_detected",
                        triggered_by,
                        reconciliation=result.model_dump(mode="json"),
                    )
            else:
                current.state = "VERIFIED" if result.status == "MATCH" else "DIVERGED"
                current.updated_at = self.clock.now()
                self.save(conn, current)
                self.event(
                    conn,
                    current,
                    "sync_reconciled",
                    triggered_by,
                    reconciliation=result.model_dump(mode="json"),
                )
        return self.get(intent.id)

    def summary(self, run_id: str) -> list[dict]:
        output = []
        with self.repo.connect() as conn:
            for intent in self.list(run_id):
                reconciled = conn.execute(
                    "SELECT * FROM reconciliations WHERE intent_id=? ORDER BY checked_at DESC,rowid DESC LIMIT 1",
                    (intent.id,),
                ).fetchone()
                reconciliation = dict(reconciled) if reconciled else None
                if reconciliation:
                    reconciliation["diffs"] = json.loads(reconciliation["diffs"])
                attempts = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM outbox_attempts WHERE intent_id=? ORDER BY attempt",
                        (intent.id,),
                    )
                ]
                output.append(
                    intent.model_dump(mode="json")
                    | {"reconciliation": reconciliation, "attempt_log": attempts}
                )
        return output
