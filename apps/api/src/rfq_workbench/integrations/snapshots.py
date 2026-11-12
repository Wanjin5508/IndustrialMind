"""Immutable source reads and pure cross-checks used by costing and dispatch."""

import json
from datetime import date, timedelta
from uuid import uuid4

from rfq_workbench.canonical import canonical_hash, canonical_json, normalize
from rfq_workbench.contracts import ConfirmedInputs
from rfq_workbench.errors import DomainError
from rfq_workbench.integrations.contracts import SourceSnapshot
from rfq_workbench.integrations.ports import HttpError, TransportError

COST_FIELDS = (
    "part_number",
    "plant",
    "currency",
    "unit",
    "material",
    "approved",
    "valid_from",
    "valid_until",
    "material_unit",
    "machine_hourly",
    "cycle_hours",
    "setup_hours",
    "finish_unit",
    "inspection_hourly",
    "inspection_hours",
    "target_margin",
)


KNOWN_COST_METADATA = {
    "origin_type",
    "notice",
    "rules_version",
    "catalog_version",
    "rate_version",
    "template_version",
    "project_id",
    "roles",
    "approved_revisions",
    "product_family",
    "blank",
    "applicability",
    "routing",
    "pending",
}


class SnapshotService:
    def __init__(self, repo, settings, systems, clock):
        self.repo, self.settings, self.systems, self.clock = repo, settings, systems, clock

    def save(self, run_id: str, raw, required: tuple[str, ...]) -> SourceSnapshot:
        if any(key not in raw.payload or raw.payload[key] is None for key in required):
            raise DomainError("SNAPSHOT_SCHEMA_DRIFT", "来源缺少关键字段，请核对接口。")
        payload = {key: raw.payload[key] for key in required}
        now = self.clock.now()
        if raw.as_of.tzinfo is None or raw.as_of > now + timedelta(seconds=5):
            raise DomainError("SNAPSHOT_SCHEMA_DRIFT", "来源时间无法确认。")
        snapshot = SourceSnapshot(
            id=uuid4().hex,
            run_id=run_id,
            ref=raw.ref,
            source_version=raw.version,
            as_of=raw.as_of,
            fetched_at=now,
            ttl_seconds=self.settings.snapshot_ttl_seconds,
            payload=normalize(payload),
            payload_sha256=canonical_hash(payload),
            adapter_version="sqlite-fake-v1",
            mapping_version="source-v1",
            unknown_fields=sorted(
                set(raw.payload)
                - set(required)
                - (KNOWN_COST_METADATA if raw.ref.entity == "cost_reference" else set())
            ),
        )
        value = snapshot.model_dump(mode="json")
        with self.repo.connect() as conn:
            conn.execute(
                "INSERT INTO source_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value["id"],
                    run_id,
                    raw.ref.system,
                    raw.ref.entity,
                    raw.ref.external_id,
                    raw.ref.plant,
                    raw.version,
                    value["as_of"],
                    value["fetched_at"],
                    value["ttl_seconds"],
                    canonical_json(value["payload"]),
                    value["payload_sha256"],
                    value["adapter_version"],
                    value["mapping_version"],
                    canonical_json(value["unknown_fields"]),
                ),
            )
            if snapshot.unknown_fields:
                self.repo.append_event(
                    conn,
                    run_id,
                    "system-dispatcher",
                    "source_schema_warning",
                    {
                        "snapshot_id": snapshot.id,
                        "unknown_fields": snapshot.unknown_fields,
                        "system": raw.ref.system,
                    },
                )
        return snapshot

    def get(self, snapshot_id: str) -> SourceSnapshot:
        with self.repo.connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_snapshots WHERE id=?", (snapshot_id,)
            ).fetchone()
        if not row:
            raise DomainError("SNAPSHOT_UNAVAILABLE", "来源快照不存在。", 409)
        value = dict(row)
        value["ref"] = {key: value.pop(key) for key in ("system", "entity", "external_id", "plant")}
        for key in ("payload", "unknown_fields"):
            value[key] = json.loads(value[key])
        return SourceSnapshot.model_validate(value)

    def acquire(self, body: dict, ref: dict) -> list[SourceSnapshot]:
        inputs = ConfirmedInputs.model_validate(body["inputs"])
        try:
            plm = self.save(
                body["id"],
                self.systems.plm.read_item_revision(inputs.part_number, inputs.plant),
                ("part_number", "plant", "revisions"),
            )
            erp = self.save(
                body["id"],
                self.systems.erp.read_cost_reference(inputs.material, inputs.plant),
                COST_FIELDS,
            )
            mes = self.save(
                body["id"],
                self.systems.mes.read_feedback(inputs.part_number, inputs.plant),
                (
                    "part_number",
                    "plant",
                    "sample_id",
                    "revision",
                    "operation",
                    "planned_minutes_per_part",
                    "observed_minutes_per_part",
                ),
            )
        except (HttpError, TransportError) as exc:
            raise DomainError(
                "SNAPSHOT_UNAVAILABLE", "模拟来源当前不可用，请稍后重试。", 409
            ) from exc
        self.validate([plm, erp, mes], body, ref)
        return [plm, erp, mes]

    def validate(self, snapshots: list[SourceSnapshot], body: dict, ref: dict) -> None:
        now = self.clock.now()
        for snapshot in snapshots:
            if (
                snapshot.run_id != body["id"]
                or now >= snapshot.fetched_at + timedelta(seconds=snapshot.ttl_seconds)
                or canonical_hash(snapshot.payload) != snapshot.payload_sha256
            ):
                raise DomainError("SNAPSHOT_STALE", "来源快照已过期，请重新计价。", 409)
            if (
                snapshot.payload["part_number"] != body["inputs"]["part_number"]
                or snapshot.payload["plant"] != body["inputs"]["plant"]
            ):
                raise DomainError("SOURCE_CONFLICT", "图纸、PLM 与 ERP 的料号或工厂不一致。")
        by_system = {s.ref.system: s for s in snapshots}
        try:
            released = [
                row["revision"]
                for row in by_system["PLM"].payload["revisions"]
                if row["status"] == "RELEASED"
                and date.fromisoformat(row["valid_from"])
                <= now.date()
                <= date.fromisoformat(row["valid_until"])
            ]
            expected = ref | {"part_number": ref["applicability"]["part_number"]}
            if by_system["ERP"].payload != {key: expected[key] for key in COST_FIELDS}:
                raise DomainError("SOURCE_CONFLICT", "ERP 成本来源与当前受控参考不一致。")
        except (KeyError, TypeError, ValueError) as exc:
            raise DomainError("SNAPSHOT_SCHEMA_DRIFT", "来源字段格式或生效期无法确认。") from exc
        if released != [body["inputs"]["drawing_revision"]]:
            raise DomainError("REVISION_CONFLICT", "图纸版次与 PLM 当前发布版次不一致。")

    def probe(self, body: dict, ids: list[str] | None = None) -> None:
        identifiers = ids if ids is not None else body.get("snapshot_refs", {}).get("ids", [])
        if not identifiers:
            raise DomainError("SNAPSHOT_STALE", "请重新计价以取得来源凭证。", 409)
        for identifier in identifiers:
            snapshot = self.get(identifier)
            if snapshot.run_id != body["id"] or self.clock.now() >= snapshot.fetched_at + timedelta(
                seconds=snapshot.ttl_seconds
            ):
                raise DomainError("SNAPSHOT_STALE", "来源快照已过期，请重新计价。", 409)
            try:
                port = getattr(self.systems, snapshot.ref.system.lower())
                version = port.probe_version(snapshot.ref)
            except Exception as exc:
                raise DomainError("SNAPSHOT_UNAVAILABLE", "来源版本暂时无法确认。", 409) from exc
            if version != snapshot.source_version:
                raise DomainError("SNAPSHOT_STALE", "来源版本已变更，请重新核对并计价。", 409)
