"""Business workflow. Models and browser requests cannot approve a task implicitly."""

from uuid import uuid4

from rfq_workbench.config import Settings
from rfq_workbench.contracts import ApproveRequest, ConfirmedInputs, RunState
from rfq_workbench.costing import calculate, fingerprint, load_reference, validate_inputs
from rfq_workbench.errors import DomainError
from rfq_workbench.evidence import REVISION_FIELDS, revision_issue
from rfq_workbench.knowledge import Knowledge
from rfq_workbench.repository import Repository, utc_now


def new_run(title: str, mode: str) -> dict:
    return {
        "id": uuid4().hex,
        "project_id": "PM-DEMO",
        "version": 1,
        "created_at": utc_now(),
        "title": title,
        "state": RunState.RECEIVED,
        "mode": mode,
        "inputs": None,
        "fields": {},
        "sources": [],
        "documents": [],
        "issues": [],
        "quote": None,
        "approval": None,
        "export": None,
        "draft_counter": 0,
        "metrics": {},
    }


def clear_results(body: dict) -> None:
    body.update(quote=None, approval=None, export=None)
    if "orchestration" in body:
        body["orchestration"].update(result=None, pause=None)


def require_intent(body: dict, allowed: tuple[str, ...]) -> None:
    """Legacy runs are quote tasks; explicit review/search tasks cannot escalate."""
    if body.get("orchestration", {}).get("intent", "prepare_quote") not in allowed:
        raise DomainError("INTENT_BOUNDARY", "This operation is outside the selected task.", 403)


def input_hash(body: dict) -> str:
    return fingerprint(
        {
            "inputs": body["inputs"],
            "fields": body["fields"],
            "documents": [{"id": d["id"], "sha256": d["sha256"]} for d in body["documents"]],
        }
    )


def run_snapshot(body: dict) -> dict:
    """Keep generated document bytes behind the authenticated download route."""
    if body["export"]:
        return {**body, "export": {"filename": body["export"]["filename"]}}
    return body


class Workflow:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repo = repository

    def create_confirmed(self, inputs: ConfirmedInputs, actor: str) -> dict:
        ref, _ = load_reference(self.settings.reference_path)
        body = new_run(inputs.part_number, "MANUAL_CONFIRMED")
        body["inputs"] = inputs.model_dump(mode="json")
        body["issues"] = [i.model_dump() for i in validate_inputs(inputs, ref)]
        body["state"] = RunState.NEEDS_ENGINEERING_REVIEW if body["issues"] else RunState.VALIDATING
        return self.repo.create(body, actor)

    def change_inputs(
        self, run_id: str, version: int, inputs: ConfirmedInputs, reason: str, actor: str
    ) -> dict:
        def change(body: dict) -> dict:
            require_intent(body, ("prepare_quote",))
            if body["documents"]:
                raise DomainError(
                    "USE_FIELD_CORRECTION",
                    "For file tasks, correct fields with source-aware review.",
                )
            old = body["inputs"]
            ref, _ = load_reference(self.settings.reference_path)
            clear_results(body)
            body["inputs"] = inputs.model_dump(mode="json")
            body["issues"] = [i.model_dump() for i in validate_inputs(inputs, ref)]
            body["state"] = (
                RunState.NEEDS_ENGINEERING_REVIEW if body["issues"] else RunState.VALIDATING
            )
            return {
                "before": old,
                "after": body["inputs"],
                "reason": reason,
                "approval_invalidated": True,
            }

        return self.repo.update(run_id, version, actor, "inputs_changed", change)

    def cost(self, run_id: str, version: int, actor: str) -> dict:
        def change(body: dict) -> dict:
            require_intent(body, ("prepare_quote",))
            if body["state"] == RunState.EXTRACTING:
                raise DomainError("TASK_BUSY", "Wait for extraction to complete.", 409)
            if body["inputs"] is None or any(i["severity"] == "blocking" for i in body["issues"]):
                raise DomainError("REVIEW_REQUIRED", "Resolve blocking issues before calculating.")
            inputs = ConfirmedInputs.model_validate(body["inputs"])
            if body["documents"]:
                from rfq_workbench.file_workflow import check_fields

                check_fields(body, self.settings)
                if body["issues"]:
                    raise DomainError(
                        "REVIEW_REQUIRED", "Current template requires engineering review."
                    )
            ref, ref_hash, basis = Knowledge(self.settings).resolve(inputs, actor)
            clear_results(body)
            body["draft_counter"] += 1
            quote = calculate(inputs, ref, ref_hash, input_hash(body), body["draft_counter"])
            quote.knowledge_basis = basis
            quote.routing = ref["routing"]
            body["quote"] = quote.model_dump(mode="json")
            body["state"] = RunState.READY_FOR_APPROVAL
            return {
                "draft_version": quote.draft_version,
                "input_hash": quote.input_hash,
                "reference_hash": ref_hash,
                "input_version": body["version"],
                "output_version": body["version"] + 1,
                "output_hash": fingerprint(body["quote"]),
            }

        return self.repo.update(run_id, version, actor, "costed", change)

    def references_match(self, body: dict, actor: str) -> bool:
        """Check a draft snapshot without invalidating it on a read-only request."""
        if body["quote"] is None:
            return False
        if body["documents"] and any(
            revision_issue(key, body["fields"].get(key, {})) for key in REVISION_FIELDS
        ):
            return False
        try:
            _, ref_hash, _ = Knowledge(self.settings).resolve(
                ConfirmedInputs.model_validate(body["inputs"]), actor
            )
            return body["quote"]["reference_hash"] == ref_hash and body["quote"][
                "input_hash"
            ] == input_hash(body)
        except DomainError:
            return False

    def quote_snapshot(self, run_id: str, version: int) -> dict:
        body = self.repo.get(run_id)
        require_intent(body, ("prepare_quote",))
        if body["version"] != version:
            raise DomainError("VERSION_CONFLICT", "Task changed. Refresh before retrying.", 409)
        if body["quote"] is None:
            raise DomainError("NO_QUOTE", "Calculate a valid draft first.")
        return body

    def check_fresh(self, run_id: str, version: int, actor: str) -> dict:
        body = self.quote_snapshot(run_id, version)
        if not self.references_match(body, actor):

            def invalidate(current: dict) -> dict:
                clear_results(current)
                current["state"] = RunState.VALIDATING
                return {"reason": "Input or reference snapshot is no longer valid"}

            self.repo.update(run_id, version, actor, "dependency_invalidated", invalidate)
            raise DomainError(
                "STALE_DRAFT", "Input or reference changed. Recalculate and approve again.", 409
            )
        return body

    def download_snapshot(self, run_id: str, version: int, actor: str) -> dict:
        body = self.quote_snapshot(run_id, version)
        if not self.references_match(body, actor):
            raise DomainError("STALE_DRAFT", "草案依据已变更，请重新计价并审批。", 409)
        export = body["export"]
        approval = body["approval"]
        if (
            body["state"] != RunState.EXPORTED_DRAFT
            or not export
            or not approval
            or approval.get("quote_hash") != fingerprint(body["quote"])
            or export.get("quote") != body["quote"]
            or export.get("approval") != approval
        ):
            raise DomainError("NO_EXPORT", "请先审批并生成当前版本的导出文件。", 409)
        return body

    def approve(self, run_id: str, request: ApproveRequest, actor: str) -> dict:
        self.check_fresh(run_id, request.expected_version, actor)

        def change(body: dict) -> dict:
            if body["state"] != RunState.READY_FOR_APPROVAL or any(
                i["severity"] == "blocking" for i in body["issues"]
            ):
                raise DomainError("REVIEW_REQUIRED", "This task is not ready for approval.")
            body["approval"] = {
                "actor": actor,
                "at": utc_now(),
                "note": request.note,
                "quote_hash": fingerprint(body["quote"]),
                "draft_version": body["quote"]["draft_version"],
                "internal_only": True,
                "technical_confirmed": True,
            }
            body["state"] = RunState.APPROVED_DRAFT
            return body["approval"]

        return self.repo.update(run_id, request.expected_version, actor, "approved", change)

    def export(self, run_id: str, version: int, actor: str) -> dict:
        current = self.check_fresh(run_id, version, actor)
        if current["state"] == RunState.EXPORTED_DRAFT and current["export"]:
            return current

        def change(body: dict) -> dict:
            quote = body["quote"]
            approval = body["approval"]
            if (
                body["state"] != RunState.APPROVED_DRAFT
                or not approval
                or approval["quote_hash"] != fingerprint(quote)
            ):
                raise DomainError(
                    "APPROVAL_REQUIRED",
                    "An authorized approval of this exact draft is required.",
                    403,
                )
            lines = [
                "# INTERNAL BUDGET DRAFT",
                "",
                "SYNTHETIC DEMO DATA - NOT A CUSTOMER OFFER",
                "",
                f"Task: {run_id}",
                f"Part: {body['inputs']['part_number']}",
                f"Quantity: {quote['quantity']}",
                f"Unit price: EUR {quote['unit_price']}",
                f"Total: EUR {quote['total_price']}",
                "",
                "## Material requirements (synthetic)",
            ]
            lines += [
                f"- {row['code']} / {row['material']} / {row['specification']}: "
                f"{row['quantity_per_part']} {row['unit']}/part, "
                f"{row['batch_quantity']} {row['unit']}/batch "
                f"({row['template_version']}, {row['catalog_version']})"
                for row in quote.get("material_lines", [])
            ]
            lines += [
                "",
                "## Cost basis",
            ]
            lines += [
                f"- {line['description']}: {line['unit_cost']} EUR/part ({line['basis']})"
                for line in quote["lines"]
            ]
            lines += ["", "## Pending / excluded"] + [f"- {item}" for item in quote["pending"]]
            lines += [
                "",
                f"Approved by: {approval['actor']}",
                f"Input hash: {quote['input_hash']}",
                f"Reference hash: {quote['reference_hash']}",
                f"Draft version: {quote['draft_version']}",
                "Knowledge basis: "
                + ", ".join(
                    k["source_id"] + "@" + k["version"] for k in quote.get("knowledge_basis", [])
                ),
            ]
            replay_source = body["metrics"].get("replay_source")
            if body["mode"] == "REPLAY" and replay_source:
                lines.append(f"Extraction source: REPLAY / {replay_source['id']}")
            body["export"] = {
                "filename": f"internal-draft-{run_id[:8]}-v{quote['draft_version']}.md",
                "markdown": "\n".join(lines) + "\n",
                "quote": quote,
                "approval": approval,
            }
            body["state"] = RunState.EXPORTED_DRAFT
            return {"draft_version": quote["draft_version"], "kind": "INTERNAL_DRAFT"}

        return self.repo.update(run_id, version, actor, "exported", change)
