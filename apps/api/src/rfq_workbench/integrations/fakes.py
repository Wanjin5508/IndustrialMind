"""Stateful simulated systems in an independent SQLite database."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from rfq_workbench.canonical import canonical_hash, canonical_json
from rfq_workbench.clock import Clock, SystemClock
from rfq_workbench.config import Settings
from rfq_workbench.integrations.contracts import ExternalRef
from rfq_workbench.integrations.faults import FaultPlan
from rfq_workbench.integrations.ports import HttpError, RawRecord, TransportError


def transport_fault(kind: str | None) -> None:
    if kind == "connect":
        raise TransportError("connect")
    if kind == "reset":
        raise TransportError("reset")
    if kind in {"timeout_before_commit", "timeout_after_commit", "stale_read"}:
        raise TransportError("read_timeout")
    if kind and kind.startswith("http_"):
        raise HttpError(int(kind.removeprefix("http_")), retry_after=7)


class FakeSystems:
    def __init__(self, settings: Settings, clock: Clock | None = None):
        self.path = settings.systems_path
        self.clock = clock or SystemClock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS resources (
                    system TEXT, entity TEXT, external_id TEXT, plant TEXT,
                    version INTEGER NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(system,entity,external_id,plant));
                CREATE TABLE IF NOT EXISTS drafts (
                    external_id TEXT PRIMARY KEY, business_key TEXT NOT NULL,
                    version INTEGER NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS draft_business ON drafts(business_key);
                CREATE TABLE IF NOT EXISTS deliveries (
                    key TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS faults (
                    id INTEGER PRIMARY KEY, method TEXT NOT NULL, kind TEXT NOT NULL, remaining INTEGER);
                CREATE TABLE IF NOT EXISTS calls(method TEXT, at TEXT);
            """)
        self.faults = FaultPlan(self)
        ref = json.loads(settings.reference_path.read_text("utf-8"))
        self.part = ref["applicability"]["part_number"]
        self.plant = ref["plant"]
        self.seed(
            "PLM",
            "item_revision",
            self.part,
            {
                "part_number": self.part,
                "plant": self.plant,
                "revisions": [
                    {
                        "revision": revision,
                        "status": "RELEASED",
                        "valid_from": ref["valid_from"],
                        "valid_until": ref["valid_until"],
                    }
                    for revision in ref["approved_revisions"]
                ],
            },
        )
        self.seed("ERP", "cost_reference", self.part, ref | {"part_number": self.part})
        self.seed(
            "MES",
            "work_order_feedback",
            self.part,
            {
                "sample_id": "SIM-MES-PM-BRKT-010-B-001",
                "revision": ref["approved_revisions"][0],
                "operation": "铣削与钻孔",
                "planned_minutes_per_part": "7.2",
                "observed_minutes_per_part": "8.1",
                "part_number": self.part,
                "plant": self.plant,
            },
        )
        if settings.sync_fault_plan:
            # A configuration signature prevents reseeding faults on every restart.
            signature = canonical_hash(settings.sync_fault_plan)
            with self.connect() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS fault_configs(signature TEXT PRIMARY KEY)")
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO fault_configs VALUES(?)", (signature,)
                ).rowcount
            if inserted:
                for fault in json.loads(settings.sync_fault_plan):
                    self.faults.add(fault["method"], fault["kind"], fault.get("times", 1))
        self.plm, self.erp, self.mes = FakePlm(self), FakeErp(self), FakeMes(self)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def seed(self, system: str, entity: str, key: str, payload: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO resources VALUES(?,?,?,?,1,?,?)",
                (
                    system,
                    entity,
                    key,
                    self.plant,
                    self.clock.now().isoformat(),
                    canonical_json(payload),
                ),
            )

    def record(self, row, system: str, entity: str) -> RawRecord:
        return RawRecord(
            ExternalRef(
                system=system,
                entity=entity,
                external_id=row["external_id"],
                plant=row["plant"] if "plant" in row.keys() else self.plant,
            ),
            str(row["version"]),
            datetime.fromisoformat(row["as_of"]),
            json.loads(row["payload"]),
        )

    def read(self, ref: ExternalRef) -> RawRecord:
        method = ref.system.lower() + ".read_" + ref.entity
        fault = self.faults.take(method)
        transport_fault(fault if fault != "schema_drift" else None)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM resources WHERE system=? AND entity=? AND external_id=? AND plant=?",
                (ref.system, ref.entity, ref.external_id, ref.plant),
            ).fetchone()
        if not row:
            raise HttpError(404)
        result = self.record(row, ref.system, ref.entity)
        if fault == "schema_drift":
            result.payload.pop("part_number", None)
        return result

    def replace_resource(self, ref: ExternalRef, payload: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE resources SET payload=?,version=version+1,as_of=? "
                "WHERE system=? AND entity=? AND external_id=? AND plant=?",
                (
                    canonical_json(payload),
                    self.clock.now().isoformat(),
                    ref.system,
                    ref.entity,
                    ref.external_id,
                    ref.plant,
                ),
            )

    def count_drafts(self, business_key: str | None = None) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT count(*) FROM drafts" + (" WHERE business_key=?" if business_key else ""),
                (business_key,) if business_key else (),
            ).fetchone()[0]

    def call_count(self, method: str) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT count(*) FROM calls WHERE method=?", (method,)).fetchone()[
                0
            ]


class FakePlm:
    def __init__(self, store: FakeSystems):
        self.store = store

    def read_item_revision(self, part_number: str, plant: str) -> RawRecord:
        return self.store.read(
            ExternalRef(system="PLM", entity="item_revision", external_id=part_number, plant=plant)
        )

    def probe_version(self, ref: ExternalRef) -> str:
        return self.store.read(ref).version

    def release_new_revision(self, revision: str) -> None:
        raw = self.read_item_revision(self.store.part, self.store.plant)
        for item in raw.payload["revisions"]:
            item["status"] = "SUPERSEDED"
        raw.payload["revisions"].append(
            {
                "revision": revision,
                "status": "RELEASED",
                "valid_from": "2020-01-01",
                "valid_until": "2099-12-31",
            }
        )
        self.store.replace_resource(raw.ref, raw.payload)


class FakeErp:
    lookup_consistency = "strong"
    settle_seconds = 30.0
    not_processed_statuses = frozenset()

    def __init__(self, store: FakeSystems):
        self.store = store

    def read_cost_reference(self, material: str, plant: str) -> RawRecord:
        return self.store.read(
            ExternalRef(
                system="ERP", entity="cost_reference", external_id=self.store.part, plant=plant
            )
        )

    def probe_version(self, ref: ExternalRef) -> str:
        if ref.entity == "quote_draft":
            return self.read_draft(ref.external_id).version
        return self.store.read(ref).version

    def find_by_idempotency_key(self, key: str) -> RawRecord | None:
        transport_fault(self.store.faults.take("erp.find_by_idempotency_key"))
        with self.store.connect() as conn:
            row = conn.execute("SELECT record FROM deliveries WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        value = json.loads(row["record"])
        return RawRecord(
            ExternalRef.model_validate(value["ref"]),
            value["version"],
            datetime.fromisoformat(value["as_of"]),
            value["payload"],
        )

    def find_by_business_key(self, business_key: str) -> list[RawRecord]:
        transport_fault(self.store.faults.take("erp.find_by_business_key"))
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM drafts WHERE business_key=?", (business_key,)
            ).fetchall()
        return [self.store.record(row, "ERP", "quote_draft") for row in rows]

    def read_draft(self, external_id: str) -> RawRecord:
        transport_fault(self.store.faults.take("erp.read_draft"))
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE external_id=?", (external_id,)
            ).fetchone()
        if not row:
            raise HttpError(404)
        return self.store.record(row, "ERP", "quote_draft")

    def put_draft(
        self, business_key: str, payload: dict, idempotency_key: str, if_match: str | None
    ) -> RawRecord:
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO calls VALUES(?,?)",
                ("erp.put_draft", self.store.clock.now().isoformat()),
            )
        fault = self.store.faults.take("erp.put_draft")
        if fault not in {"timeout_after_commit", "partial_write", "duplicate_delivery"}:
            transport_fault(fault)
        digest = canonical_hash(payload)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT * FROM deliveries WHERE key=?", (idempotency_key,)
            ).fetchone()
            if old:
                if old["request_sha256"] != digest:
                    raise HttpError(422)
                value = json.loads(old["record"])
                return RawRecord(
                    ExternalRef.model_validate(value["ref"]),
                    value["version"],
                    datetime.fromisoformat(value["as_of"]),
                    value["payload"],
                )
            rows = conn.execute(
                "SELECT * FROM drafts WHERE business_key=?", (business_key,)
            ).fetchall()
            if len(rows) > 1:
                raise HttpError(409)
            if rows and if_match != str(rows[0]["version"]):
                raise HttpError(412)
            if not rows and if_match is not None:
                raise HttpError(412)
            external_id = rows[0]["external_id"] if rows else uuid4().hex
            version = rows[0]["version"] + 1 if rows else 1
            written = payload | {"lines": []} if fault == "partial_write" else payload
            at = self.store.clock.now()
            conn.execute(
                "INSERT OR REPLACE INTO drafts VALUES(?,?,?,?,?)",
                (external_id, business_key, version, at.isoformat(), canonical_json(written)),
            )
            result = RawRecord(
                ExternalRef(
                    system="ERP",
                    entity="quote_draft",
                    external_id=external_id,
                    plant=self.store.plant,
                ),
                str(version),
                at,
                written,
            )
            encoded = {
                "ref": result.ref.model_dump(),
                "version": result.version,
                "as_of": at,
                "payload": written,
            }
            conn.execute(
                "INSERT INTO deliveries VALUES(?,?,?)",
                (idempotency_key, digest, canonical_json(encoded)),
            )
        if fault == "timeout_after_commit":
            raise TransportError("read_timeout")
        if fault == "duplicate_delivery":
            return self.put_draft(business_key, payload, idempotency_key, if_match)
        return result


class FakeMes:
    def __init__(self, store: FakeSystems):
        self.store = store

    def read_feedback(self, part_number: str, plant: str) -> RawRecord:
        return self.store.read(
            ExternalRef(
                system="MES", entity="work_order_feedback", external_id=part_number, plant=plant
            )
        )

    def probe_version(self, ref: ExternalRef) -> str:
        return self.store.read(ref).version
