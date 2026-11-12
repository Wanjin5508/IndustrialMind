"""Bounded, explicit routing over the existing durable task snapshot.

Creation and document attachment never invoke a model. A resume executes at most
one extraction and one calculation/search; approval remains a separate API.
"""

from rfq_workbench.contracts import KnowledgeQuery, ResumeTask, RunState, TaskRequest
from rfq_workbench.documents import ingest
from rfq_workbench.errors import DomainError
from rfq_workbench.file_workflow import FileWorkflow, check_fields
from rfq_workbench.knowledge import Knowledge
from rfq_workbench.service import Workflow, clear_results, new_run

INTENTS = ["prepare_quote", "review_drawing", "search_knowledge"]


def visible(body: dict, actor: str) -> bool:
    """Knowledge snapshots inherit their creator's role; never leak via task lists."""
    task = body.get("orchestration", {})
    return (
        not task or task.get("intent") not in (None, "search_knowledge") or task["owner"] == actor
    )


def require_file_intent(body: dict) -> None:
    if body.get("orchestration", {}).get("intent", "prepare_quote") not in INTENTS[:2]:
        raise DomainError("INTENT_BOUNDARY", "Select a file task before editing documents.", 403)


class Orchestrator:
    def __init__(self, workflow: Workflow, files: FileWorkflow):
        self.workflow, self.files = workflow, files
        self.repo, self.settings = workflow.repo, workflow.settings

    def get(self, run_id: str, actor: str) -> dict:
        body = self.repo.get(run_id)
        if not visible(body, actor):
            raise DomainError("NOT_FOUND", "Task not found in this role.", 404)
        return body

    @staticmethod
    def authorize(intent: str | None, actor: str) -> None:
        if intent in INTENTS[:2] and actor != "demo-engineer":
            raise DomainError("FORBIDDEN", "Engineering role is required for file tasks.", 403)

    def create(self, request: TaskRequest, actor: str) -> dict:
        self.authorize(request.intent, actor)
        context = {
            "intent": request.intent,
            "request": request.request,
            "owner": actor,
            "query": request.query.model_dump(mode="json") if request.query else None,
            "result": None,
            "pause": None,
        }
        if request.fixture_id:
            if request.intent not in INTENTS[:2]:
                raise DomainError("INTENT_BOUNDARY", "Fixtures require an explicit file intent.")
            body = self.files.from_fixture(request.fixture_id, actor, context)
        else:
            body = new_run(request.request or request.intent or "待澄清任务", "TASK")
            # Role scope is part of the FIRST durable snapshot, including on interruption.
            body["orchestration"] = context
            self.clarify(body)
            body = self.repo.create(body, actor)

        def route(current: dict) -> dict:
            self.clarify(current)
            return {"intent": request.intent, "route_version": "explicit-router-v1"}

        return self.repo.update(body["id"], body["version"], actor, "task_routed", route)

    @staticmethod
    def clarify(body: dict) -> bool:
        task = body["orchestration"]
        intent = task["intent"]
        missing = []
        if intent is None:
            missing = ["intent"]
        elif intent == "search_knowledge":
            if task["query"] is None:
                missing = ["query"]
        else:
            needed = {"drawing", "rfq"} if intent == "prepare_quote" else {"drawing"}
            missing = sorted(needed - {d["kind"] for d in body["documents"]})
        if missing:
            body["state"] = RunState.CLARIFICATION_REQUIRED
            task["pause"] = {
                "code": "MISSING_INPUT",
                "missing": missing,
                "message": "请选择明确任务类型或补充所列资料，再显式恢复。文本请求不会自动路由。",
                "options": INTENTS if intent is None else [],
            }
        else:
            task["pause"] = None
            body["state"] = RunState.RECEIVED
        return bool(missing)

    def attach(self, run_id: str, version: int, uploads: list[tuple], actor: str) -> dict:
        before = self.get(run_id, actor)
        if "orchestration" not in before:
            raise DomainError("LEGACY_TASK", "Use the existing controls for this legacy task.")
        require_file_intent(before)
        if before["version"] != version or before["state"] == RunState.EXTRACTING:
            raise DomainError(
                "VERSION_CONFLICT", "Refresh and wait for extraction before attaching.", 409
            )
        if not uploads:
            raise DomainError("NO_FILES", "Attach at least one RFQ or drawing.")
        parsed = [
            (kind, *ingest(self.settings.data_dir, run_id, kind, name, data))
            for kind, name, data in uploads
        ]

        def change(body: dict) -> dict:
            previous = body["documents"].copy()
            for kind, document, sources in parsed:
                old_ids = {d["id"] for d in body["documents"] if d["kind"] == kind}
                body["documents"] = [d for d in body["documents"] if d["kind"] != kind] + [document]
                body["sources"] = [
                    s for s in body["sources"] if s["document_id"] not in old_ids
                ] + sources
            clear_results(body)
            body.update(fields={}, inputs=None, issues=[], metrics={})
            self.clarify(body)
            return {
                "before": [{"id": d["id"], "sha256": d["sha256"]} for d in previous],
                "after": [{"id": d["id"], "sha256": d["sha256"]} for d in body["documents"]],
                "approval_invalidated": True,
            }

        return self.repo.update(run_id, version, actor, "task_documents_attached", change)

    def resume(self, run_id: str, request: ResumeTask, actor: str) -> dict:
        before = self.get(run_id, actor)
        if "orchestration" not in before:
            raise DomainError("LEGACY_TASK", "Use the existing controls for this legacy task.")
        task = before["orchestration"]
        intent = request.intent or task["intent"]
        self.authorize(intent, actor)
        if task["intent"] is not None and request.intent and request.intent != task["intent"]:
            raise DomainError("INTENT_IMMUTABLE", "Create a new task to change its purpose.", 409)
        if before["version"] != request.expected_version:
            raise DomainError("VERSION_CONFLICT", "Refresh before resuming.", 409)
        if before["state"] == RunState.EXTRACTING:
            raise DomainError("TASK_BUSY", "Extraction is already running.", 409)
        # Repeated resume at the approval gate must not recalculate or revoke approval.
        if before["quote"] is not None:
            return self.workflow.check_fresh(run_id, request.expected_version, actor)
        if before["state"] in {RunState.REVIEW_COMPLETED, RunState.KNOWLEDGE_COMPLETED}:
            return before

        def clarify(body: dict) -> dict:
            current = body["orchestration"]
            current["intent"] = intent
            if request.query:
                if intent != "search_knowledge":
                    raise DomainError("INTENT_BOUNDARY", "Queries belong to knowledge tasks.")
                current["query"] = request.query.model_dump(mode="json")
            current["result"] = None
            missing = self.clarify(body)
            if not missing and intent in INTENTS[:2] and not body["fields"]:
                self.files.check_attempt(body, request.mode)
            return {"intent": intent, "mode": request.mode, "pause": current["pause"]}

        body = self.repo.update(run_id, request.expected_version, actor, "task_resumed", clarify)
        if body["state"] == RunState.CLARIFICATION_REQUIRED:
            return body
        if intent == "search_knowledge":
            result = Knowledge(self.settings).search(
                KnowledgeQuery.model_validate(body["orchestration"]["query"]), actor
            )

            def found(current: dict) -> dict:
                current["orchestration"]["result"] = result
                current["state"] = RunState.KNOWLEDGE_COMPLETED
                return {"intent": intent}

            return self.repo.update(run_id, body["version"], actor, "knowledge_completed", found)
        if not body["fields"]:
            body = self.files.analyze(run_id, body["version"], request.mode, actor)

        def validate(current: dict) -> dict:
            check_fields(current, self.settings)
            return {"blocking_issues": len(current["issues"])}

        try:
            body = self.repo.update(run_id, body["version"], actor, "task_validated", validate)
        except DomainError as exc:
            if exc.status == 409:
                raise
            return self.pause(body, actor, exc.code, exc.message)
        if body["issues"]:
            return self.pause(
                body, actor, "ENGINEERING_REVIEW", "请在资料与证据中修正问题，填写依据后恢复。"
            )
        if intent == "review_drawing":

            def reviewed(current: dict) -> dict:
                current["state"] = RunState.REVIEW_COMPLETED
                current["orchestration"]["result"] = {
                    "summary": "已完成支持范围内的图纸字段与模板适用性检查。",
                    "limitations": [
                        "未审查完整 GD&T、孔位与可制造性",
                        "未确认客户 RFQ 要求、数量与交期",
                        "未生成价格或工程批准",
                    ],
                    "evidence_fields": [
                        key for key in current["fields"] if key.startswith("drawing.")
                    ],
                }
                return {"intent": intent, "price_generated": False}

            return self.repo.update(
                run_id, body["version"], actor, "drawing_review_completed", reviewed
            )
        try:
            return self.workflow.cost(run_id, body["version"], actor)
        except DomainError as exc:
            if exc.status == 409:
                raise
            return self.pause(body, actor, exc.code, exc.message)

    def pause(self, body: dict, actor: str, code: str, message: str) -> dict:
        def change(current: dict) -> dict:
            current["state"] = RunState.NEEDS_ENGINEERING_REVIEW
            current["orchestration"]["pause"] = {
                "code": code,
                "message": message,
                "missing": [],
                "options": [],
            }
            return current["orchestration"]["pause"]

        return self.repo.update(body["id"], body["version"], actor, "task_paused", change)
