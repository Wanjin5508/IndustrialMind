"""One durable observe–decide–act cycle per step request."""

import json
from dataclasses import asdict
from decimal import Decimal
from time import time
from uuid import uuid4

from rfq_workbench.agent.model import INSTRUCTIONS, ToolModel
from rfq_workbench.agent.store import AgentStore
from rfq_workbench.agent.tools import ToolGateway
from rfq_workbench.documents import asset_path, ingest
from rfq_workbench.errors import DomainError

LIMITS = {"steps": 18, "tokens": 48_000, "seconds": 180, "errors": 5}
MAX_ATTACHMENTS = 6
MODEL_TIMEOUT = 25
TRANSIENT = {"AGENT_TOOL_TIMEOUT", "MODEL_TIMEOUT", "MODEL_CONNECTION_FAILED"}
RETRYABLE_MODEL = {"AGENT_MODEL_TIMEOUT", "AGENT_MODEL_UNAVAILABLE", "AGENT_INVALID_MODEL_OUTPUT"}
MODEL_FAILURES = RETRYABLE_MODEL | {"AGENT_MODEL_ERROR", "AGENT_MODEL_NOT_CONFIGURED"}
TERMINAL = {"completed", "stopped", "exhausted"}


class AgentRunner:
    def __init__(self, workflow, files, model=None):
        self.settings = workflow.settings
        self.store = AgentStore(self.settings.data_dir / "agent.sqlite3")
        self.gateway = ToolGateway(workflow, files)
        self.model = model if model is not None else ToolModel(self.settings)

    def create(self, request: str, uploads: list, actor: str) -> dict:
        if not request.strip() and not uploads:
            raise DomainError("AGENT_NO_INPUT", "请提供请求或附件。")
        body = {
            "id": uuid4().hex,
            "actor": actor,
            "request": request.strip(),
            "status": "ready",
            "documents": [],
            "sources": [],
            "intent": None,
            "selection": {},
            "plan": [],
            "steps": [],
            "pending": None,
            "lease": None,
            "limits": LIMITS,
            "usage": {"steps": 0, "tokens": 0, "seconds": 0.0, "errors": 0},
            "estimated_cost": None,
            "cost_partial": False,
            "round": 0,
            "answers": [],
            "validated_version": None,
            "knowledge_searched": False,
            "reviewed": False,
            "review_attention": False,
            "question": None,
            "summary": None,
            "reply": None,
            "reply_version": None,
            "draft_questions": None,
            "risks": [],
        }
        documents, sources = self.parse_uploads(body["id"], uploads)
        body.update(documents=documents, sources=sources)
        return self.view(self.store.create(body))

    def parse_uploads(self, run_id: str, uploads: list) -> tuple[list, list]:
        if len(uploads) > MAX_ATTACHMENTS:
            raise DomainError("AGENT_ATTACHMENT_LIMIT", "每个任务最多六份附件。")
        documents, sources = [], []
        for name, data in uploads:
            document, evidence = ingest(self.settings.data_dir, run_id, "attachment", name, data)
            documents.append(document)
            sources.extend(evidence)
        return documents, sources

    @staticmethod
    def document_list(body: dict) -> list:
        return [
            {"id": f"D{index}", "name": item["name"], "pages": len(item["pages"])}
            for index, item in enumerate(body["documents"], 1)
        ]

    def view(self, body: dict) -> dict:
        view = {
            key: body[key]
            for key in (
                "id",
                "request",
                "status",
                "intent",
                "selection",
                "plan",
                "question",
                "answers",
                "summary",
                "reply",
                "draft_questions",
                "risks",
                "limits",
                "usage",
                "estimated_cost",
                "cost_partial",
            )
        }
        view["service_issue"] = self.model_issue(body)
        if view["service_issue"]:
            view.update(status="model_paused", question=None)
        case = self.gateway.case(body)
        view["case_id"] = body["id"] if case else None
        view["quote"] = self.gateway.quote_summary(case or {})
        view["issues"] = (case or {}).get("issues", [])
        view["documents"] = self.document_list(body)
        aliases = self.gateway.attachment_aliases(body)
        view["selection"] = {kind: aliases[doc_id] for kind, doc_id in body["selection"].items()}
        view["steps"] = [
            {key: step[key] for key in ("id", "call", "result", "error") if key in step}
            for step in body["steps"]
        ]
        if body["pending"]:
            view["pending_tool"] = (body["pending"].get("call") or {}).get("name")
        return view

    @staticmethod
    def model_issue(body: dict) -> dict | None:
        if body["status"] == "model_paused":
            return body["service_issue"]
        # Runs saved before the service pause state used a clarification placeholder.
        question = body["question"] or {}
        if body["status"] == "waiting" and question.get("code") in MODEL_FAILURES:
            return {"code": question["code"], "message": question["question"]}
        return None

    def retry(self, run_id: str, actor: str) -> dict:
        def resume(body):
            if not self.model_issue(body):
                raise DomainError("AGENT_NOT_MODEL_PAUSED", "该任务当前未暂停模型执行。", 409)
            if self.budget_reason(body):
                body.update(status="exhausted", summary="执行达到预算，已保留部分结果。")
            else:
                body.update(status="ready", model_retry_after=len(body["steps"]))
            body.update(question=None, service_issue=None)

        return self.view(self.store.update(run_id, actor, resume))

    def answer(self, run_id: str, actor: str, answer: str, uploads: list) -> dict:
        if not answer.strip() and not uploads:
            raise DomainError("AGENT_NO_ANSWER", "请填写答复或补充附件。")
        before = self.store.get(run_id, actor)
        if before["status"] != "waiting" or self.model_issue(before):
            raise DomainError("AGENT_NOT_WAITING", "该任务当前未等待答复。", 409)
        if len(before["documents"]) + len(uploads) > MAX_ATTACHMENTS:
            raise DomainError("AGENT_ATTACHMENT_LIMIT", "每个任务最多六份附件。")
        documents, sources = self.parse_uploads(run_id, uploads)

        def resume(body):
            if body["status"] != "waiting" or body["round"] != before["round"]:
                raise DomainError("AGENT_NOT_WAITING", "任务已接收答复，请刷新。", 409)
            body["documents"].extend(documents)
            body["sources"].extend(sources)
            body["answers"].append(
                {
                    "question": body["question"],
                    "text": answer.strip(),
                    "documents": [doc["id"] for doc in documents],
                }
            )
            body.update(
                status="ready",
                question=None,
                round=body["round"] + 1,
                reviewed=False,
                review_attention=False,
            )

        return self.view(self.store.update(run_id, actor, resume))

    def stop(self, run_id: str, actor: str) -> dict:
        def stop_run(body):
            if body["status"] not in TERMINAL:
                body["status"] = "stopped"

        return self.view(self.store.update(run_id, actor, stop_run))

    @staticmethod
    def budget_reason(body):
        for key, maximum in body["limits"].items():
            if body["usage"][key] >= maximum:
                return key
        return None

    @staticmethod
    def ask(body, message, code):
        body.update(
            status="waiting",
            question={
                "question": message,
                "audience": "engineer",
                "options": [],
                "code": code,
            },
        )

    def step(self, run_id: str, actor: str) -> dict:
        token = uuid4().hex
        started = time()

        def claim(body):
            if body["status"] in TERMINAL or body["status"] in {"waiting", "model_paused"}:
                return
            lease = body["lease"]
            if lease and lease["until"] > started:
                raise DomainError("AGENT_BUSY", "Agent 正在处理上一步。", 409)
            if lease:
                body["usage"]["seconds"] += max(0, lease["until"] - lease["started"])
            if self.budget_reason(body):
                body.update(status="exhausted", lease=None)
                return
            body.update(
                status="running", lease={"token": token, "until": started + 65, "started": started}
            )
            if not body["pending"]:
                body["pending"] = {"id": uuid4().hex, "round": body["round"]}

        body = self.store.update(run_id, actor, claim)
        if body["status"] != "running":
            return self.view(body)

        def owned(current):
            if (current["lease"] or {}).get("token") != token:
                raise DomainError("AGENT_LEASE_LOST", "该步骤已由新的执行者接管。", 409)

        def save(change):
            def guarded(current):
                owned(current)
                change(current)

            return self.store.update(run_id, actor, guarded)

        def timeout():
            remaining = body["limits"]["seconds"] - body["usage"]["seconds"] - (time() - started)
            if remaining < 1:
                raise DomainError("AGENT_TIME_LIMIT", "执行已达到时间预算。")
            return min(MODEL_TIMEOUT, self.settings.model_timeout, remaining)

        error = None
        result = None
        try:
            if "call" not in body["pending"]:
                context, image = self.context(body)
                tools = self.gateway.schemas(body)
                # Reserve input plus maximum output before a network call.
                encoded = json.dumps([context, tools], ensure_ascii=False).encode("utf-8")
                estimated = len(encoded) + len(INSTRUCTIONS) + 1700
                if image:
                    estimated += 1700
                if body["usage"]["tokens"] + estimated > LIMITS["tokens"]:
                    raise DomainError("AGENT_TOKEN_LIMIT", "执行已达到 token 预算。")

                def record_observation(current):
                    current["pending"]["input"] = {"context": context, "tools": tools}
                    current["usage"]["steps"] += 1
                    current["usage"]["tokens"] += estimated

                body = save(record_observation)
                if body["status"] == "stopped":
                    raise DomainError("AGENT_STOPPED", "Agent 已停止。")
                call = self.model.decide(context, tools, timeout(), image)

                def record_call(current):
                    current["pending"]["call"] = asdict(call)
                    if call.tokens:
                        current["usage"]["tokens"] += call.tokens - estimated
                    self.add_cost(current, call.cost)

                body = save(record_call)
            call = body["pending"]["call"]
            if body["status"] == "stopped":
                raise DomainError("AGENT_STOPPED", "Agent 已停止。")
            if body["usage"]["tokens"] >= LIMITS["tokens"]:
                raise DomainError("AGENT_TOKEN_LIMIT", "执行已达到 token 预算。")
            case = self.gateway.case(body)
            if "case_version" not in body["pending"]:

                def record_case_version(current):
                    current["pending"]["case_version"] = case["version"] if case else None

                body = save(record_case_version)
            self.check_repeat(body, call)
            current = self.store.get(run_id, actor)
            owned(current)
            if current["status"] == "stopped":
                raise DomainError("AGENT_STOPPED", "Agent 已停止。")
            result = self.gateway.execute(
                body, call["name"], call["arguments"], body["pending"]["id"], timeout()
            )
        except DomainError as exc:
            error = {"code": exc.code, "message": exc.message}

        def complete(current):
            pending = current["pending"]
            pending.update(result=result, error=error)
            current["steps"].append(pending)
            if current["status"] != "stopped":
                for key in (
                    "intent",
                    "selection",
                    "plan",
                    "validated_version",
                    "knowledge_searched",
                    "reviewed",
                    "review_attention",
                    "question",
                    "summary",
                    "reply",
                    "reply_version",
                    "draft_questions",
                    "risks",
                ):
                    current[key] = body[key]
                current["status"] = "ready" if body["status"] == "running" else body["status"]
            if result and result.get("usage"):
                current["usage"]["tokens"] += result["usage"]
                self.add_cost(current, result.get("estimated_cost"))
            current["usage"]["seconds"] += max(0, time() - started)
            if error and error["code"] != "AGENT_STOPPED":
                current["usage"]["errors"] += 1
                if "input" in pending and not pending.get("call"):
                    current["cost_partial"] = True
            if current["status"] not in TERMINAL and current["status"] != "waiting":
                if self.budget_reason(current) or (
                    error and error["code"] in ("AGENT_TOKEN_LIMIT", "AGENT_TIME_LIMIT")
                ):
                    current["status"] = "exhausted"
                    current["summary"] = "执行达到预算，已保留部分结果。"
                elif error and error["code"] in (
                    "AGENT_REPEATED_TOOL",
                    "AGENT_FORBIDDEN_TOOL",
                ):
                    self.ask(current, error["message"], error["code"])
                elif error and error["code"] in MODEL_FAILURES:
                    previous = len(current["steps"]) - 2
                    previous_error = (
                        current["steps"][previous].get("error") or {}
                        if previous >= current.get("model_retry_after", 0)
                        else {}
                    )
                    if (
                        error["code"] not in RETRYABLE_MODEL
                        or previous_error.get("code") in RETRYABLE_MODEL
                    ):
                        current.update(status="model_paused", service_issue=error)
                elif (
                    error and error["code"] in TRANSIENT and self.repeated(current, pending["call"])
                ):
                    self.ask(current, "来源重试后仍不可用，请工程师复核。", error["code"])
            current.update(pending=None, lease=None)

        return self.view(save(complete))

    def context(self, body: dict) -> tuple[dict, bytes | None]:
        recent = []
        last_tool_result = None
        for step in body["steps"][-3:]:
            call = step.get("call")
            result = step.get("result")
            if call:
                last_tool_result = result
            if result and len(json.dumps(result, ensure_ascii=False)) > 2200:
                result = {
                    "excerpt": json.dumps(result, ensure_ascii=False)[:2200],
                    "truncated": True,
                }
            recent.append(
                {
                    "tool": call["name"] if call else None,
                    "arguments": call["arguments"] if call and step.get("error") else None,
                    "result": result,
                    "error": step.get("error"),
                }
            )
        case = self.gateway.case(body) or {}
        image = None
        if last_tool_result and last_tool_result.get("image"):
            ref = last_tool_result["image"]
            image = asset_path(
                self.settings.data_dir, body, ref["document_id"], ref["page"]
            ).read_bytes()
        return {
            "request": body["request"],
            "intent": body["intent"],
            "documents": self.document_list(body),
            "selection": {
                kind: self.gateway.attachment_aliases(body)[doc_id]
                for kind, doc_id in body["selection"].items()
            },
            "plan": body["plan"],
            "answers": body["answers"][-2:],
            "recent_tools": recent,
            "issues": case.get("issues", [])[:8],
            "key_facts": {
                key: field.get("value")
                for key, field in case.get("fields", {}).items()
                if key
                in {
                    "rfq.required_revision",
                    "drawing.revision",
                    "rfq.quantity",
                    "drawing.material",
                    "drawing.part_number",
                }
            },
            "draft": self.gateway.quote_summary(case),
            "validated": body["validated_version"] is not None,
            "knowledge_searched": body["knowledge_searched"],
            "reply_saved": bool(body["reply"]),
            "package_reviewed": body["reviewed"],
            "remaining_steps": max(0, LIMITS["steps"] - body["usage"]["steps"]),
        }, image

    @staticmethod
    def repeated(body, call):
        return [
            step
            for step in body["steps"]
            if step["round"] == body["round"]
            and step.get("call")
            and step["id"] != body["pending"]["id"]
            and (step["call"]["name"], step["call"]["arguments"])
            == (call["name"], call["arguments"])
            and (
                call["name"] not in {"run_validation", "get_case_state"}
                or step.get("case_version") == body["pending"].get("case_version")
            )
        ]

    def check_repeat(self, body, call):
        matches = self.repeated(body, call)
        if matches and not (
            len(matches) == 1 and (matches[0]["error"] or {}).get("code") in TRANSIENT
        ):
            raise DomainError("AGENT_REPEATED_TOOL", "同一工具已重复执行，请工程师提供新的依据。")

    @staticmethod
    def add_cost(body, amount):
        if amount is None:
            body["cost_partial"] = True
        else:
            body["estimated_cost"] = str(Decimal(body["estimated_cost"] or "0") + Decimal(amount))
