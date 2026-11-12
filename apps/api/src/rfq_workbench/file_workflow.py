"""File-to-draft workflow, evidence checks and explicit human corrections."""

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from rfq_workbench.config import ROOT, Settings
from rfq_workbench.contracts import (
    ConfirmedInputs,
    CorrectFields,
    ExtractedValue,
    Extraction,
    RunState,
)
from rfq_workbench.costing import fingerprint, load_reference, validate_inputs
from rfq_workbench.documents import ingest
from rfq_workbench.errors import DomainError
from rfq_workbench.evidence import REVISION_FIELDS, revision_issue, verify
from rfq_workbench.extraction import LABELS, LocalTextProvider, OpenAIProvider, Provider
from rfq_workbench.replay import ReplayProvider
from rfq_workbench.repository import Repository, utc_now
from rfq_workbench.service import clear_results, new_run, require_intent

MAX_ATTEMPTS = 3
TRANSIENT_ERRORS = {
    "MODEL_TIMEOUT",
    "MODEL_CONNECTION_FAILED",
    "MODEL_RATE_LIMIT",
    "MODEL_REQUEST_FAILED",
    "EXTRACTION_INTERRUPTED",
}

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
    review_only = body.get("orchestration", {}).get("intent") == "review_drawing"
    required = {key for key in REQUIRED if key.startswith("drawing.")} if review_only else REQUIRED
    for key in sorted(required):
        field = fields.get(key, {})
        if not field.get("value"):
            issue(
                "MISSING_FIELD",
                key,
                "A required fact is missing; supply evidence or a documented human confirmation.",
            )
        elif not field.get("source_valid") and not field.get("human_confirmed"):
            issue("INVALID_SOURCE", key, "Value has no valid document evidence.")
        elif field.get("evidence", {}).get("status") in {
            "TEXT_MISMATCH",
            "AMBIGUOUS",
        } and not field.get("human_confirmed"):
            issue(field["evidence"]["status"], key, field["evidence"]["message"])
        elif field.get("needs_visual_review") and not field.get("human_confirmed"):
            issue("VISUAL_REVIEW", key, "Page-level visual fact requires engineering confirmation.")
        elif key in REVISION_FIELDS and (problem := revision_issue(key, field)):
            issues.append(problem.model_dump())
    body["inputs"] = None
    if review_only:
        # Drawing review never fabricates RFQ quantity, currency or customer revision.
        if not issues:
            ref, _ = load_reference(settings.reference_path)
            expected_fields = {
                "part_number": ref["applicability"]["part_number"],
                "material": ref["material"],
                "unit": ref["unit"],
                "surface_finish": ref["applicability"]["surface_finish"],
            }
            for key, expected in expected_fields.items():
                if fields[f"drawing.{key}"]["value"].casefold() != expected.casefold():
                    issue(
                        "DRAWING_SCOPE",
                        f"drawing.{key}",
                        "Drawing is outside the approved template scope.",
                    )
            if fields["drawing.revision"]["value"] not in ref["approved_revisions"]:
                issue(
                    "REVISION_UNAPPROVED",
                    "drawing.revision",
                    "Drawing revision has no approved template.",
                )
            for key, expected in ref["applicability"]["dimensions"].items():
                try:
                    actual = Decimal(fields[f"drawing.{key}"]["value"])
                    matches = actual.is_finite() and actual == Decimal(expected)
                except InvalidOperation:
                    matches = False
                if not matches:
                    issue(
                        "TEMPLATE_GEOMETRY",
                        f"drawing.{key}",
                        "Geometry is outside the approved template.",
                    )
        body["issues"] = issues
        body["state"] = RunState.NEEDS_ENGINEERING_REVIEW if issues else RunState.VALIDATING
        return
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
            "replay": ReplayProvider(),
        }

    def create(
        self,
        rfq_name: str,
        rfq_data: bytes,
        drawing_name: str,
        drawing_data: bytes,
        actor: str,
        orchestration: dict | None = None,
    ) -> dict:
        body = new_run(rfq_name, "FILES")
        if orchestration is not None:
            body["orchestration"] = orchestration
        for kind, name, data in [
            ("rfq", rfq_name, rfq_data),
            ("drawing", drawing_name, drawing_data),
        ]:
            document, sources = ingest(self.settings.data_dir, body["id"], kind, name, data)
            body["documents"].append(document)
            body["sources"].extend(sources)
        return self.repo.create(body, actor)

    def from_fixture(self, case_id: str, actor: str, orchestration: dict | None = None) -> dict:
        case = next((c for c in fixture_catalog() if c["id"] == case_id), None)
        if not case:
            raise DomainError("NOT_FOUND", "Unknown development fixture.", 404)
        rfq, drawing = ROOT / case["rfq"], ROOT / case["drawing"]
        # Only server-owned catalog paths are read; no client path or expected answer is accepted.
        return self.create(
            f"{case_id}-rfq.md",
            rfq.read_bytes(),
            drawing.name,
            drawing.read_bytes(),
            actor,
            orchestration,
        )

    def lease_seconds(self) -> float:
        return 4 * self.settings.model_timeout + 30

    @staticmethod
    def document_hashes(body: dict) -> dict:
        return {d["kind"]: d["sha256"] for d in body["documents"]}

    def check_attempt(self, body: dict, mode: str) -> None:
        require_intent(body, ("prepare_quote", "review_drawing"))
        if body["state"] == RunState.EXTRACTING:
            raise DomainError("TASK_BUSY", "识别正在进行。若任务中断，请使用恢复操作。", 409)
        if not body["documents"]:
            raise DomainError("NO_FILES", "请先上传资料。")
        if (
            mode == "live"
            and not self.settings.simulate_model_timeout
            and not (self.settings.model_key and self.settings.model_name)
        ):
            raise DomainError("MODEL_NOT_CONFIGURED", "在线识别未配置，请选择其他识别方式。")
        if mode == "replay":
            ReplayProvider().capture(body)
        budget = body.get("extraction_budget", {})
        if budget.get("input_hash") == fingerprint(self.document_hashes(body)) and (
            budget["attempts"].get(mode, 0) >= MAX_ATTEMPTS
        ):
            raise DomainError(
                "RETRY_LIMIT", "当前资料的识别重试已用完，请更换识别方式或更新资料。", 409
            )

    @staticmethod
    def record_failure(body: dict, error: DomainError) -> dict:
        attempt = body["extraction_attempt"]
        retryable = error.code in TRANSIENT_ERRORS or (
            error.code == "MODEL_HTTP_ERROR" and (error.metrics.get("provider_status") or 0) >= 500
        )
        final = not retryable or attempt["number"] >= MAX_ATTEMPTS
        body["state"] = RunState.FAILED_FINAL if final else RunState.FAILED_RETRYABLE
        if final:
            body["extraction_budget"]["attempts"][attempt["mode"]] = MAX_ATTEMPTS
        body["metrics"] = error.metrics
        body["issues"] = [
            {
                "code": error.code,
                "field": "extraction",
                "message": error.message,
                "severity": "blocking",
            }
        ]
        return {
            **attempt,
            "code": error.code,
            "metrics": error.metrics,
            "output_version": body["version"] + 1,
            "retryable": not final,
        }

    def recover(self, run_id: str, version: int, actor: str) -> dict:
        before = self.repo.get(run_id)
        last_start = next(
            (
                e["occurred_at"]
                for e in reversed(before["events"])
                if e["action"] == "extraction_started"
            ),
            before["created_at"],
        )

        def recover_expired(body: dict) -> dict:
            require_intent(body, ("prepare_quote", "review_drawing"))
            if body["state"] != RunState.EXTRACTING:
                raise DomainError("NOT_EXTRACTING", "当前任务没有待恢复的识别操作。", 409)
            attempt = body.get("extraction_attempt")
            deadline = (
                datetime.fromisoformat(attempt["lease_until"])
                if attempt
                else datetime.fromisoformat(last_start) + timedelta(seconds=self.lease_seconds())
            )
            if datetime.now(UTC) < deadline:
                raise DomainError("TASK_BUSY", "识别尚在等待期限内，请稍后刷新。", 409)
            if attempt is None:
                # Pre-M6 tasks have an audit timestamp but no stored lease or budget.
                mode = body["mode"].lower()
                digest = fingerprint(self.document_hashes(body))
                body["extraction_attempt"] = {
                    "number": 1,
                    "mode": mode,
                    "input_hash": digest,
                    "input_version": body["version"] - 1,
                    "lease_until": deadline.isoformat(),
                }
                body["extraction_budget"] = {"input_hash": digest, "attempts": {mode: 1}}
            return self.record_failure(
                body,
                DomainError("EXTRACTION_INTERRUPTED", "识别已中断，请重新处理。"),
            )

        return self.repo.update(run_id, version, actor, "extraction_recovered", recover_expired)

    def analyze(self, run_id: str, version: int, mode: str, actor: str) -> dict:
        def claim(body: dict) -> dict:
            self.check_attempt(body, mode)
            documents = self.document_hashes(body)
            digest = fingerprint(documents)
            budget = body.get("extraction_budget", {})
            if budget.get("input_hash") != digest:
                budget = {"input_hash": digest, "attempts": {}}
            number = budget["attempts"].get(mode, 0) + 1
            budget["attempts"][mode] = number
            attempt = {
                "mode": mode,
                "number": number,
                "limit": MAX_ATTEMPTS,
                "input_version": body["version"],
                "input_hash": digest,
                "documents": documents,
                "pipeline_version": "extraction-v2",
                "lease_until": (
                    datetime.now(UTC) + timedelta(seconds=self.lease_seconds())
                ).isoformat(),
            }
            clear_results(body)
            body.update(
                state=RunState.EXTRACTING,
                fields={},
                issues=[],
                inputs=None,
                mode=mode.upper(),
                metrics={},
                extraction_budget=budget,
                extraction_attempt=attempt,
            )
            return attempt

        claimed = self.repo.update(run_id, version, actor, "extraction_started", claim)
        claimed_version = version + 1
        try:
            if claimed["version"] != claimed_version:
                raise DomainError("VERSION_CONFLICT", "任务已更新，请刷新。", 409)
            result = self.providers[mode].extract(claimed)

            def complete(body: dict) -> dict:
                validated = Extraction.model_validate(result.fields.model_dump())
                for kind, values in validated.model_dump().items():
                    for key, field in values.items():
                        body["fields"][f"{kind}.{key}"] = verify(
                            body, kind, key, LABELS[kind][key], ExtractedValue.model_validate(field)
                        )
                body["metrics"] = result.metrics
                check_fields(body, self.settings)
                body["extraction_budget"]["attempts"].pop(mode)
                return {
                    **body["extraction_attempt"],
                    "output_version": body["version"] + 1,
                    "output_hash": fingerprint(validated.model_dump(mode="json")),
                    "metrics": result.metrics,
                    "blocking_issues": len(body["issues"]),
                }

            return self.repo.update(run_id, claimed_version, actor, "extracted", complete)
        except Exception as exc:
            if isinstance(exc, DomainError) and exc.code == "VERSION_CONFLICT":
                raise
            error = (
                exc
                if isinstance(exc, DomainError)
                else DomainError("EXTRACTION_FAILED", "识别失败，请检查资料。", 502)
            )
            self.repo.update(
                run_id,
                claimed_version,
                actor,
                "extraction_failed",
                lambda body: self.record_failure(body, error),
            )
            raise error from exc

    def correct(self, run_id: str, request: CorrectFields, actor: str) -> dict:
        allowed = {f"{kind}.{key}" for kind, labels in LABELS.items() for key in labels}
        if not request.values or set(request.values) - allowed:
            raise DomainError("INVALID_FIELDS", "Provide supported extraction fields only.")

        def change(body: dict) -> dict:
            require_intent(body, ("prepare_quote", "review_drawing"))
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
                normalized = ExtractedValue(value=value, source_id=None).value
                if key in REVISION_FIELDS and normalized != field.get("original_value"):
                    raise DomainError(
                        "REVISION_OVERRIDE",
                        "不能修改文件版次。请上传正确版本的 RFQ 或图纸后重新识别。",
                        409,
                    )
                changes[key] = {"before": field["value"], "after": normalized}
                field.update(
                    value=normalized,
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
        require_intent(previous, ("prepare_quote", "review_drawing"))
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
