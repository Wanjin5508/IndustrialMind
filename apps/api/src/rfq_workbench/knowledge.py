"""Permission-first deterministic retrieval and controlled reference selection.

History can explain a decision but cannot supply price. Only the server-owned
manufacturing payload is executable; clients cannot submit a source path or ID
to override the selection policy.
"""

import json
import re
from datetime import date

from pydantic import ValidationError

from rfq_workbench.config import Settings
from rfq_workbench.contracts import ConfirmedInputs, KnowledgeQuery, KnowledgeRecord
from rfq_workbench.costing import fingerprint, load_reference, validate_inputs
from rfq_workbench.errors import DomainError


class Knowledge:
    def __init__(self, settings: Settings):
        self.settings = settings

    def visible(self, actor: str) -> list[KnowledgeRecord]:
        try:
            records = [
                KnowledgeRecord.model_validate(row)
                for row in json.loads(self.settings.knowledge_path.read_text("utf-8"))
            ]
            if len({r.id for r in records}) != len(records):
                raise ValueError("Duplicate knowledge identity")
        except (OSError, ValueError, TypeError, ValidationError) as exc:
            raise DomainError(
                "INVALID_KNOWLEDGE", "Controlled knowledge registry is unavailable or invalid."
            ) from exc
        # Fixed demo entitlements belong to the server, never to search parameters.
        return [
            r
            for r in records
            if r.project_id == "PM-DEMO"
            and r.plant == "DE-DEMO"
            and r.product_family == "mounting-bracket"
            and actor in r.roles
        ]

    def inspect(
        self,
        record: KnowledgeRecord,
        query: KnowledgeQuery,
        today: date,
        reference: dict | None = None,
    ) -> dict:
        reasons = []
        if not record.approved:
            reasons.append("UNAPPROVED")
        if not record.valid_from <= today <= record.valid_until:
            reasons.append("OUTSIDE_VALIDITY")
        for field in ("part_number", "material", "unit", "currency"):
            if getattr(record, field) != getattr(query, field):
                reasons.append("MISMATCH_" + field.upper())
        if query.revision not in record.revisions:
            reasons.append("MISMATCH_REVISION")
        if record.kind == "history" or record.payload_source != "manufacturing":
            reasons.append("REFERENCE_ONLY")
        else:
            try:
                ref = (
                    reference
                    if reference is not None
                    else load_reference(self.settings.reference_path, today)[0]
                )
                expected = {
                    "catalog": ref["catalog_version"],
                    "template": ref["template_version"],
                    "rate": ref["rate_version"],
                }[record.kind]
                if record.version != expected:
                    reasons.append("PAYLOAD_VERSION_MISMATCH")
                if (
                    any(getattr(query, key) != ref[key] for key in ("material", "unit", "currency"))
                    or query.part_number != ref["applicability"]["part_number"]
                    or query.revision not in ref["approved_revisions"]
                ):
                    reasons.append("PAYLOAD_NOT_APPLICABLE")
            except DomainError:
                reasons.append("INVALID_MANUFACTURING_PAYLOAD")
        return record.model_dump(mode="json") | {
            "eligible_for_cost": not reasons,
            "reasons": reasons,
            "usage": "APPROVED_BASIS" if not reasons else "REFERENCE_ONLY",
            "source_id": record.id,
            "record_hash": fingerprint(record.model_dump(mode="json")),
        }

    def search(self, query: KnowledgeQuery, actor: str) -> dict:
        tokens = re.findall(r"[\w-]+", query.query.casefold())
        results = []
        for record in self.visible(actor):
            if query.kind and record.kind != query.kind:
                continue
            haystack = " ".join(
                (record.id, record.title, record.content, record.material, record.part_number)
            ).casefold()
            if tokens and not all(token in haystack for token in tokens):
                continue
            results.append(self.inspect(record, query, date.today()))
        results.sort(key=lambda r: (not r["eligible_for_cost"], r["kind"], r["id"]))
        return {
            "scope": {
                "project_id": "PM-DEMO",
                "plant": "DE-DEMO",
                "product_family": "mounting-bracket",
            },
            "query": query.model_dump(),
            "results": results,
            "notice": "SYNTHETIC DEMO. Reference-only history never supplies current prices.",
        }

    def get(self, record_id: str, query: KnowledgeQuery, actor: str) -> dict:
        record = next((r for r in self.visible(actor) if r.id == record_id), None)
        if not record:
            raise DomainError("NOT_FOUND", "Knowledge record not found in authorized scope.", 404)
        return self.inspect(record, query, date.today())

    def resolve(self, inputs: ConfirmedInputs, actor: str) -> tuple[dict, str, list[dict]]:
        ref, payload_hash = load_reference(self.settings.reference_path)
        issues = validate_inputs(inputs, ref)
        if issues:
            raise DomainError(issues[0].code, issues[0].message)
        query = KnowledgeQuery(
            part_number=inputs.part_number,
            material=inputs.material,
            unit=inputs.unit,
            currency=inputs.currency,
            revision=inputs.drawing_revision,
        )
        selected = []
        today = date.today()
        candidates = [self.inspect(r, query, today, ref) for r in self.visible(actor)]
        for kind in ("catalog", "template", "rate"):
            matches = [r for r in candidates if r["kind"] == kind and r["eligible_for_cost"]]
            if len(matches) != 1:
                raise DomainError(
                    "NO_UNIQUE_BASIS",
                    f"Exactly one authorized, approved, applicable {kind} is required.",
                )
            selected.append(matches[0])
        snapshot_hash = fingerprint({"payload_hash": payload_hash, "selected": selected})
        return ref, snapshot_hash, selected
