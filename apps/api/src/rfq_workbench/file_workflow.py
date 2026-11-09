"""File-to-draft workflow, evidence checks and explicit human corrections."""

import json
import re
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from rfq_workbench.config import ROOT, Settings
from rfq_workbench.contracts import ConfirmedInputs, CorrectFields, RunState
from rfq_workbench.costing import load_reference, validate_inputs
from rfq_workbench.documents import ingest
from rfq_workbench.errors import DomainError
from rfq_workbench.extraction import LABELS, LocalTextProvider, OpenAIProvider, Provider
from rfq_workbench.repository import Repository, utc_now
from rfq_workbench.service import clear_results, new_run

REQUIRED = {
    "rfq.part_number",
    "rfq.required_revision",
    "rfq.quantity",
    "rfq.currency",
    "drawing.part_number",
    "drawing.revision",
    "drawing.material",
    "drawing.unit",
    "drawing.width_mm",
    "drawing.height_mm",
    "drawing.thickness_mm",
    "drawing.hole_diameter_mm",
    "drawing.surface_finish",
}


def fixture_catalog() -> list[dict]:
    return json.loads((ROOT / "fixtures/manifests/dev.json").read_text("utf-8"))["cases"]


def check_fields(body: dict, settings: Settings) -> None:
    """Fail closed on incomplete facts and unsupported template applicability."""
    issues = []

    def issue(code: str, field: str, message: str):
        issues.append({"code": code, "field": field, "message": message, "severity": "blocking"})

    fields = body["fields"]
    for key in sorted(REQUIRED):
        field = fields.get(key, {})
        if not field.get("value"):
            issue(
                "MISSING_FIELD",
                key,
                "A required fact is missing; supply evidence or a documented human confirmation.",
            )
        elif not field.get("source_valid") and not field.get("human_confirmed"):
            issue("INVALID_SOURCE", key, "Value has no valid document evidence.")
        elif field.get("needs_visual_review") and not field.get("human_confirmed"):
            issue("VISUAL_REVIEW", key, "Page-level visual fact requires engineering confirmation.")
    body["inputs"] = None
    if not issues:

        def value(key: str) -> str:
            return fields[key]["value"]

        quantity = value("rfq.quantity")
        if not re.fullmatch(r"[0-9]+", quantity):
            issue(
                "INVALID_QUANTITY", "rfq.quantity", "Quantity must be an integer from 1 to 100,000."
            )
        else:
            try:
                inputs = ConfirmedInputs(
                    part_number=value("rfq.part_number"),
                    required_revision=value("rfq.required_revision"),
                    drawing_revision=value("drawing.revision"),
                    quantity=int(quantity),
                    currency=value("rfq.currency"),
                    material=value("drawing.material"),
                    unit=value("drawing.unit"),
                )
                ref, _ = load_reference(settings.reference_path)
                issues.extend(i.model_dump() for i in validate_inputs(inputs, ref))
                if value("drawing.part_number") != inputs.part_number:
                    issue(
                        "PART_CONFLICT",
                        "drawing.part_number",
                        "RFQ and drawing part numbers differ.",
                    )
                if inputs.part_number != ref["applicability"]["part_number"]:
                    issue(
                        "NO_TEMPLATE",
                        "rfq.part_number",
                        "No approved template for this part number.",
                    )
                for key, expected in ref["applicability"]["dimensions"].items():
                    try:
                        actual = Decimal(value(f"drawing.{key}"))
                        matches = actual.is_finite() and actual == Decimal(expected)
                    except InvalidOperation:
                        matches = False
                    if not matches:
                        issue(
                            "TEMPLATE_GEOMETRY",
                            f"drawing.{key}",
                            "Geometry is outside the single approved demo template.",
                        )
                if (
                    value("drawing.surface_finish").casefold()
                    != ref["applicability"]["surface_finish"].casefold()
                ):
                    issue(
                        "TEMPLATE_FINISH",
                        "drawing.surface_finish",
                        "Finish has no approved cost template.",
                    )
                body["inputs"] = inputs.model_dump(mode="json")
            except ValidationError:
                issue(
                    "INVALID_INPUT",
                    "rfq.quantity",
                    "Input is outside the supported schema or quantity range.",
                )
    body["issues"] = issues
    body["state"] = RunState.NEEDS_ENGINEERING_REVIEW if issues else RunState.VALIDATING


class FileWorkflow:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repo = repository
        self.providers: dict[str, Provider] = {
            "local_text": LocalTextProvider(),
            "live": OpenAIProvider(settings),
        }

    def create(
        self, rfq_name: str, rfq_data: bytes, drawing_name: str, drawing_data: bytes, actor: str
    ) -> dict:
        body = new_run(rfq_name, "FILES")
        for kind, name, data in [
            ("rfq", rfq_name, rfq_data),
            ("drawing", drawing_name, drawing_data),
        ]:
            document, sources = ingest(self.settings.data_dir, body["id"], kind, name, data)
            body["documents"].append(document)
            body["sources"].extend(sources)
        return self.repo.create(body, actor)

    def from_fixture(self, case_id: str, actor: str) -> dict:
        case = next((c for c in fixture_catalog() if c["id"] == case_id), None)
        if not case:
            raise DomainError("NOT_FOUND", "Unknown development fixture.", 404)
        rfq, drawing = ROOT / case["rfq"], ROOT / case["drawing"]
        # Only server-owned catalog paths are read; no client path or expected answer is accepted.
        return self.create(
            f"{case_id}-{rfq.name}", rfq.read_bytes(), drawing.name, drawing.read_bytes(), actor
        )

    def analyze(self, run_id: str, version: int, mode: str, actor: str) -> dict:
        if mode == "live" and (not self.settings.model_key or not self.settings.model_name):
            raise DomainError(
                "MODEL_NOT_CONFIGURED", "Configure model credentials on the backend and restart."
            )

        def claim(body: dict) -> dict:
            if body["state"] == RunState.EXTRACTING:
                raise DomainError("TASK_BUSY", "Extraction is already running.", 409)
            if not body["documents"]:
                raise DomainError("NO_FILES", "Upload RFQ and drawing before extraction.")
            clear_results(body)
            body.update(
                state=RunState.EXTRACTING,
                fields={},
                issues=[],
                inputs=None,
                mode=mode.upper(),
                metrics={},
            )
            return {"mode": mode}

        claimed = self.repo.update(run_id, version, actor, "extraction_started", claim)
        try:
            result = self.providers[mode].extract(claimed)

            def complete(body: dict) -> dict:
                source_map = {s["id"]: s for s in body["sources"]}
                kinds = {d["id"]: d["kind"] for d in body["documents"]}
                for kind, values in result.fields.model_dump().items():
                    for key, field in values.items():
                        source = source_map.get(field["source_id"])
                        valid = bool(source and kinds[source["document_id"]] == kind)
                        # A page citation can support a visual fact, but cannot auto-verify it.
                        supported_text = (
                            valid
                            and bool(field["value"])
                            and bool(
                                re.search(
                                    r"^"
                                    + re.escape(LABELS[kind][key])
                                    + r":\s*"
                                    + re.escape(field["value"])
                                    + r"\s*$",
                                    source["text"],
                                    re.I | re.M,
                                )
                            )
                        )
                        body["fields"][f"{kind}.{key}"] = field | {
                            "original_value": field["value"],
                            "source_valid": valid,
                            "needs_visual_review": valid and not supported_text,
                            "human_confirmed": False,
                        }
                body["metrics"] = result.metrics
                check_fields(body, self.settings)
                return {"metrics": result.metrics, "blocking_issues": len(body["issues"])}

            return self.repo.update(run_id, claimed["version"], actor, "extracted", complete)
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, DomainError)
                else DomainError(
                    "EXTRACTION_FAILED", "Extraction failed. Review input and retry.", 502
                )
            )

            def failed(body: dict) -> dict:
                body["state"] = RunState.FAILED_RETRYABLE
                body["issues"] = [
                    {
                        "code": error.code,
                        "field": "extraction",
                        "message": error.message,
                        "severity": "blocking",
                    }
                ]
                return {"code": error.code}

            self.repo.update(run_id, claimed["version"], actor, "extraction_failed", failed)
            raise error from exc

    def correct(self, run_id: str, request: CorrectFields, actor: str) -> dict:
        allowed = {f"{kind}.{key}" for kind, labels in LABELS.items() for key in labels}
        if not request.values or set(request.values) - allowed:
            raise DomainError("INVALID_FIELDS", "Provide supported extraction fields only.")

        def change(body: dict) -> dict:
            if body["state"] == RunState.EXTRACTING:
                raise DomainError("TASK_BUSY", "Wait for extraction before editing.", 409)
            if not body["fields"]:
                raise DomainError("NO_EXTRACTION", "Extract documents before reviewing fields.")
            changes = {}
            for key, value in request.values.items():
                if len(value) > 200:
                    raise DomainError(
                        "INVALID_FIELDS", "Field values must be at most 200 characters."
                    )
                field = body["fields"][key]
                changes[key] = {"before": field["value"], "after": value.strip() or None}
                field.update(
                    value=value.strip() or None,
                    human_confirmed=True,
                    correction={"actor": actor, "reason": request.reason, "at": utc_now()},
                )
            clear_results(body)
            check_fields(body, self.settings)
            return {"changes": changes, "reason": request.reason, "approval_invalidated": True}

        return self.repo.update(run_id, request.expected_version, actor, "fields_corrected", change)

    def replace_drawing(
        self, run_id: str, version: int, name: str, data: bytes, reason: str, actor: str
    ) -> dict:
        previous = self.repo.get(run_id)
        if previous["version"] != version:
            raise DomainError("VERSION_CONFLICT", "Refresh task before replacing a drawing.", 409)
        if previous["state"] == RunState.EXTRACTING:
            raise DomainError("TASK_BUSY", "Wait for extraction before replacing a drawing.", 409)
        if not previous["documents"]:
            raise DomainError("NO_FILES", "This task has no source documents.")
        document, sources = ingest(self.settings.data_dir, run_id, "drawing", name, data)

        def change(body: dict) -> dict:
            old = [d for d in body["documents"] if d["kind"] == "drawing"]
            old_ids = {d["id"] for d in old}
            body["documents"] = [d for d in body["documents"] if d["kind"] != "drawing"] + [
                document
            ]
            body["sources"] = [
                s for s in body["sources"] if s["document_id"] not in old_ids
            ] + sources
            clear_results(body)
            body.update(fields={}, inputs=None, issues=[], metrics={}, state=RunState.RECEIVED)
            return {
                "old_documents": old,
                "new_document": document,
                "reason": reason,
                "approval_invalidated": True,
            }

        return self.repo.update(run_id, version, actor, "drawing_replaced", change)
