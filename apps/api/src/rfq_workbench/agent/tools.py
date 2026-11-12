"""Capability gateway for agent tools. The model supplies data, not Python calls."""

import json
from dataclasses import replace

from rfq_workbench.contracts import ExtractedValue, KnowledgeQuery
from rfq_workbench.documents import asset_path
from rfq_workbench.errors import DomainError
from rfq_workbench.evidence import verify
from rfq_workbench.extraction import LABELS, OpenAIProvider
from rfq_workbench.file_workflow import check_fields
from rfq_workbench.integrations.ports import HttpError, TransportError
from rfq_workbench.knowledge import Knowledge
from rfq_workbench.service import clear_results, new_run

TOOLS = {
    "get_case_state": ("Read current documents, field evidence, blockers and draft state.", {}),
    "read_document": (
        "Read text and source IDs from at most two pages. IDs are listed in context.",
        {"document_id": str, "first_page": int, "last_page": int},
    ),
    "view_page": (
        "Request one PDF page image as the next model input.",
        {"document_id": str, "page": int},
    ),
    "classify_attachments": (
        "Choose task intent and assign document roles. Copy exact IDs from documents. "
        "Use empty ID when absent. Plan is up to three short steps. "
        "Intent: prepare_quote, review_drawing, search_knowledge.",
        {"intent": str, "rfq_id": str, "drawing_id": str, "plan": list},
    ),
    "extract_fields": (
        "Extract document facts. Mode local_text or live. Use empty document_id/field "
        "and page 0 for all local fields. Live requires one document and page.",
        {"mode": str, "document_id": str, "page": int, "field": str},
    ),
    "search_knowledge": (
        "Find authorized knowledge and applicable or reference-only sources. "
        "Use an empty query for all records in the current scope.",
        {"query": str},
    ),
    "lookup_plm_revision": (
        "Read published revisions for the observed part from simulated PLM.",
        {},
    ),
    "lookup_erp_rate": ("Read matching costs and rates from simulated ERP.", {}),
    "run_validation": ("Check field evidence, revision, units and template scope.", {}),
    "calculate_cost": (
        "Generate an internal quote draft using the backend's deterministic calculation.",
        {},
    ),
    "draft_reply": (
        "Save a customer reply draft, grounded in the current case. This never sends it.",
        {"text": str},
    ),
    "draft_questions": ("Save questions for customer or engineering review.", {"text": str}),
    "flag_risk": (
        "Append a risk to the package. Include a field or source ID when available.",
        {"text": str, "source_id": str},
    ),
    "ask_human": (
        "Pause for an answer. Audience is customer or engineer. Options can be empty.",
        {"question": str, "audience": str, "options": list},
    ),
    "review_package": (
        "Independently check the saved package against actual case and quote values.",
        {},
    ),
    "finish": (
        "Finish with a short evidence-based summary of the reviewed package.",
        {"summary": str},
    ),
}

FIELD_KEYS = {f"{kind}.{key}" for kind, labels in LABELS.items() for key in labels}
MAX_ARGUMENT = 1600


class ToolGateway:
    def __init__(self, workflow, files):
        self.workflow = workflow
        self.repo = workflow.repo
        self.settings = workflow.settings
        self.files = files

    def case(self, state: dict) -> dict | None:
        try:
            return self.repo.get(state["id"])
        except DomainError as exc:
            if exc.status != 404:
                raise
            return None

    def schemas(self, state: dict) -> list[dict]:
        names = []
        intent = state.get("intent")
        case = self.case(state)
        version = case["version"] if case else None
        has_new_docs = bool(
            state["round"]
            and state["answers"][-1]["documents"]
            and not any(
                step.get("call", {}).get("name") == "classify_attachments"
                and not step.get("error")
                and step["round"] == state["round"]
                for step in state["steps"]
            )
        )
        if state["reviewed"] or state["review_attention"]:
            review_tools = ["flag_risk", "ask_human"]
            if state["reviewed"]:
                review_tools.insert(0, "finish")
            return [self.schema(name, state) for name in review_tools]
        if (
            intent == "prepare_quote"
            and case
            and case["quote"]
            and not case["issues"]
            and not has_new_docs
        ):
            names = ["review_package"] if state["reply"] else ["draft_reply"]
            return [self.schema(name, state) for name in [*names, "flag_risk", "ask_human"]]
        successful = {
            step["call"]["name"]
            for step in state["steps"]
            if step.get("call")
            and not step.get("error")
            and step["round"] == state["round"]
            and step.get("case_version") == version
        }
        if not case or not intent or has_new_docs:
            names.append("classify_attachments")
        if self.unread_ids(state, "read_document"):
            names.append("read_document")
        if self.unread_ids(state, "view_page"):
            names.append("view_page")
        if case:
            fields = case["fields"]
            needs_evidence = not fields or any(
                issue["code"]
                in (
                    "MISSING_FIELD",
                    "INVALID_SOURCE",
                    "VISUAL_REVIEW",
                    "TEXT_MISMATCH",
                    "AMBIGUOUS",
                )
                for issue in case["issues"]
            )
            if needs_evidence:
                names.append("extract_fields")
            if fields:
                names += [
                    name
                    for name in ("get_case_state", "run_validation", "lookup_plm_revision")
                    if name not in successful
                ]
            if intent == "prepare_quote" and case["inputs"] and not case["issues"]:
                if not state["knowledge_searched"]:
                    names.append("search_knowledge")
                if "lookup_erp_rate" not in successful:
                    names.append("lookup_erp_rate")
                if state["validated_version"] == version and state["knowledge_searched"]:
                    names.append("calculate_cost")
                if case["quote"] and not state["reply"]:
                    names.append("draft_reply")
        if intent == "search_knowledge":
            names.append("search_knowledge")
        review_ready = (
            (intent == "prepare_quote" and case and case["quote"] and state["reply"])
            or (intent == "review_drawing" and state["validated_version"] is not None)
            or (intent == "search_knowledge" and state["knowledge_searched"])
        )
        if review_ready and not state["reviewed"]:
            names.append("review_package")
        names += ["draft_questions", "flag_risk", "ask_human"]
        return [self.schema(name, state) for name in names]

    @staticmethod
    def unread_ids(state: dict, action: str) -> list[str]:
        visited = set()
        for step in state["steps"]:
            if (
                step["round"] != state["round"]
                or step.get("error")
                or step.get("call", {}).get("name") != action
            ):
                continue
            result = step.get("result") or {}
            if action == "read_document" and "document_id" in result:
                visited.update((result["document_id"], p["page"]) for p in result["pages"])
            elif "image" in result:
                visited.add((result["image"]["document_id"], result["image"]["page"]))
        return [
            f"D{index}"
            for index, document in enumerate(state["documents"], 1)
            if any(
                (document["id"], page["number"]) not in visited
                and (action == "read_document" or page["has_preview"])
                for page in document["pages"]
            )
        ]

    @staticmethod
    def attachment_aliases(state: dict) -> dict:
        return {doc["id"]: f"D{index}" for index, doc in enumerate(state["documents"], 1)}

    @staticmethod
    def schema(name: str, state: dict) -> dict:
        description, args = TOOLS[name]
        types = {str: "string", int: "integer", list: "array"}
        properties = {
            key: {
                "type": types[kind],
                **(
                    {"items": {"type": "string", "maxLength": 160}, "maxItems": 4}
                    if kind is list
                    else {}
                ),
                **({"maxLength": MAX_ARGUMENT} if kind is str else {}),
            }
            for key, kind in args.items()
        }
        document_ids = [f"D{index}" for index in range(1, len(state["documents"]) + 1)]
        if name in {"read_document", "view_page"}:
            document_ids = ToolGateway.unread_ids(state, name)
        for key in ("rfq_id", "drawing_id", "document_id"):
            if key in properties:
                allow_empty = key != "document_id" or name == "extract_fields"
                properties[key]["enum"] = ([""] if allow_empty else []) + document_ids
        enums = {
            "intent": ["prepare_quote", "review_drawing", "search_knowledge"],
            "mode": ["local_text", "live"],
            "audience": ["customer", "engineer"],
            "field": ["", *sorted(FIELD_KEYS)],
        }
        for key, values in enums.items():
            if key in properties:
                properties[key]["enum"] = values
        for key in ("first_page", "last_page", "page"):
            if key in properties:
                properties[key]["minimum"] = 0 if name == "extract_fields" else 1
                properties[key]["maximum"] = 3
        return {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(args),
                "additionalProperties": False,
            },
        }

    def execute(self, state: dict, name: str, args: dict, step_id: str, timeout: float) -> dict:
        allowed = {tool["name"] for tool in self.schemas(state)}
        if name == "extract_fields" and self.extraction_applied(self.case(state) or {}, step_id):
            allowed.add(name)
        if name == "calculate_cost" and self.cost_applied(
            self.case(state) or {}, state["validated_version"]
        ):
            allowed.add(name)
        if name not in allowed:
            raise DomainError("AGENT_FORBIDDEN_TOOL", "该工具在当前任务中不可用。")
        _, fields = TOOLS[name]
        if not isinstance(args, dict) or set(args) != set(fields):
            raise DomainError("AGENT_BAD_ARGUMENTS", "工具参数与接口契约不符。")
        for key, kind in fields.items():
            value = args[key]
            if (
                type(value) is not kind
                or (isinstance(value, str) and len(value) > MAX_ARGUMENT)
                or (
                    isinstance(value, list)
                    and (
                        len(value) > 4
                        or any(not isinstance(item, str) or len(item) > 160 for item in value)
                    )
                )
            ):
                raise DomainError("AGENT_BAD_ARGUMENTS", "工具参数类型或长度无效。")
        try:
            result = getattr(self, name)(state, args, step_id, timeout)
        except HttpError as exc:
            if exc.status in (429, 500, 502, 503, 504):
                raise DomainError("AGENT_TOOL_TIMEOUT", "来源暂不可用，可重试一次。") from exc
            raise DomainError("AGENT_SOURCE_ERROR", "来源系统未返回有效结果。") from exc
        except TransportError as exc:
            raise DomainError("AGENT_TOOL_TIMEOUT", "来源暂不可用，可重试一次。") from exc
        if len(json.dumps(result, ensure_ascii=False)) > 6500:
            return {"code": "RESULT_LIMIT", "message": "结果超限，请缩小页或字段范围。"}
        return result

    def get_case_state(self, state, args, step_id, timeout):
        case = self.case(state)
        return {"intent": state.get("intent"), "case": self.case_summary(case)}

    @staticmethod
    def case_summary(case):
        if case is None:
            return None
        return {
            "version": case["version"],
            "state": case["state"],
            "fields": {
                key: {
                    "value": value["value"],
                    "source": value.get("source_id"),
                    "evidence": (value.get("evidence") or {}).get("status"),
                }
                for key, value in case["fields"].items()
            },
            "issues": case["issues"][:12],
            "quote": ToolGateway.quote_summary(case),
        }

    @staticmethod
    def quote_summary(case):
        quote = case.get("quote")
        if quote is None:
            return None
        return {
            key: quote[key]
            for key in ("quantity", "unit_price", "total_price", "rate_version", "draft_version")
        }

    @staticmethod
    def document(state, doc_id):
        for index, document in enumerate(state["documents"], 1):
            if doc_id in (f"D{index}", document["id"]):
                return document
        matching_names = [item for item in state["documents"] if item["name"] == doc_id]
        if len(matching_names) == 1:
            return matching_names[0]
        raise DomainError("AGENT_DOCUMENT_SCOPE", "附件不属于当前任务。")

    def read_document(self, state, args, step_id, timeout):
        doc = self.document(state, args["document_id"])
        first, last = args["first_page"], args["last_page"]
        if not 1 <= first <= last <= len(doc["pages"]) or last - first > 1:
            raise DomainError("AGENT_PAGE_RANGE", "每次可读取有效范围内最多两页。")
        return {
            "document_id": doc["id"],
            "pages": [
                {
                    "page": page["number"],
                    "source_id": f"{doc['id']}:p{page['number']}",
                    "text": page["text"][:1200],
                    "truncated": len(page["text"]) > 1200,
                }
                for page in doc["pages"][first - 1 : last]
            ],
        }

    def view_page(self, state, args, step_id, timeout):
        doc = self.document(state, args["document_id"])
        asset_path(self.settings.data_dir, state, doc["id"], args["page"])
        return {
            "image": {"document_id": doc["id"], "page": args["page"]},
            "source_id": f"{doc['id']}:p{args['page']}",
        }

    def classify_attachments(self, state, args, step_id, timeout):
        intent = args["intent"]
        if intent not in {"prepare_quote", "review_drawing", "search_knowledge"}:
            raise DomainError("AGENT_INVALID_INTENT", "请选择受支持的任务类型。")
        selected = {
            kind: self.document(state, args[f"{kind}_id"])["id"]
            for kind in ("rfq", "drawing")
            if args[f"{kind}_id"]
        }
        if len(set(selected.values())) != len(selected):
            raise DomainError("AGENT_BAD_ARGUMENTS", "同一附件不能同时作 RFQ 和图纸。")
        documents = [
            self.document(state, doc_id) | {"kind": kind} for kind, doc_id in selected.items()
        ]
        if any(d["kind"] == "drawing" and d["suffix"] != ".pdf" for d in documents):
            raise DomainError("AGENT_DRAWING_TYPE", "图纸需要 PDF 附件。")
        needed = (
            {"rfq", "drawing"}
            if intent == "prepare_quote"
            else ({"drawing"} if intent == "review_drawing" else set())
        )
        case = self.case(state)
        if case and case["orchestration"]["intent"] != intent:
            raise DomainError("INTENT_IMMUTABLE", "已有工程记录的任务保持原意图。")
        state["intent"] = intent
        state["selection"] = selected
        state["plan"] = args["plan"]
        missing = sorted(needed - selected.keys())
        aliases = self.attachment_aliases(state)
        public_selection = {kind: aliases[doc_id] for kind, doc_id in selected.items()}
        if missing or not needed:
            return {"intent": intent, "selection": public_selection, "missing": missing}
        documents = [doc for doc in documents if doc["kind"] in needed]
        doc_ids = {doc["id"] for doc in documents}
        sources = [source for source in state["sources"] if source["document_id"] in doc_ids]
        if case is None:
            body = new_run(state["request"][:120] or "RFQ Agent", "FILES")
            body.update(id=state["id"], documents=documents, sources=sources)
            body["orchestration"] = {
                "intent": intent,
                "request": state["request"],
                "owner": state["actor"],
                "query": None,
                "result": None,
                "pause": None,
            }
            self.repo.create(body, state["actor"])
        elif {d["id"] for d in case["documents"]} != doc_ids:

            def replace_docs(body):
                clear_results(body)
                body.update(
                    documents=documents,
                    sources=sources,
                    fields={},
                    inputs=None,
                    issues=[],
                    state="RECEIVED",
                )
                return {"step_id": step_id, "selection": selected}

            self.repo.update(
                case["id"], case["version"], state["actor"], "agent_documents_bound", replace_docs
            )
            state.update(
                validated_version=None, reviewed=False, reply=None, knowledge_searched=False
            )
        return {"intent": intent, "selection": public_selection, "missing": []}

    def extract_fields(self, state, args, step_id, timeout):
        case = self.case(state)
        assert case is not None
        if self.extraction_applied(case, step_id):
            state.update(
                validated_version=None, reviewed=False, reply=None, knowledge_searched=False
            )
            return self.case_summary(case)
        mode, doc_id, page, field = (args[key] for key in ("mode", "document_id", "page", "field"))
        if doc_id:
            doc_id = self.document(state, doc_id)["id"]
        if mode not in {"local_text", "live"} or (field and field not in FIELD_KEYS):
            raise DomainError("AGENT_BAD_ARGUMENTS", "识别模式或字段无效。")
        documents = [doc for doc in case["documents"] if not doc_id or doc["id"] == doc_id]
        if not documents or (
            page and (len(documents) != 1 or not 1 <= page <= len(documents[0]["pages"]))
        ):
            raise DomainError("AGENT_PAGE_RANGE", "聚焦抽取需使用当前图纸或 RFQ 的有效页。")
        if mode == "live" and (not doc_id or not page):
            raise DomainError("AGENT_PAGE_RANGE", "在线重抽每次仅允许一页。")
        kinds = {doc["kind"] for doc in documents}
        if field and field.split(".")[0] not in kinds:
            raise DomainError("AGENT_BAD_ARGUMENTS", "聚焦字段与附件类型不一致。")
        scoped = case | {
            "documents": [
                doc | {"pages": [p for p in doc["pages"] if not page or p["number"] == page]}
                for doc in documents
            ],
            "sources": [
                s
                for s in case["sources"]
                if s["document_id"] in {d["id"] for d in documents}
                and (not page or s["page"] == page)
            ],
        }
        # The existing live provider uses absolute page numbers for page images.
        if mode == "live":
            provider = OpenAIProvider(replace(self.settings, model_timeout=timeout))
            snippets = []
            remaining = 2400
            for source in scoped["sources"][:24]:
                text = source["text"][: min(1200, remaining)]
                snippets.append(source | {"text": text})
                remaining -= len(text)
                if remaining == 0:
                    break
            scoped["sources"] = snippets
            result = provider.extract(scoped, preview_body=case)
        else:
            result = self.files.providers["local_text"].extract(scoped)
        allowed_sources = {source["id"] for source in scoped["sources"]}

        def save(body):
            clear_results(body)
            for kind, values in result.fields.model_dump().items():
                for key, data in values.items():
                    name = f"{kind}.{key}"
                    previous = body["fields"].get(name, {})
                    if (
                        kind not in kinds
                        or (field and field != name)
                        or previous.get("human_confirmed")
                    ):
                        continue
                    candidate = ExtractedValue.model_validate(data)
                    if candidate.source_id and candidate.source_id not in allowed_sources:
                        continue
                    verified = verify(body, kind, key, LABELS[kind][key], candidate)
                    if previous.get("evidence", {}).get("status") == "TEXT_VERIFIED":
                        continue
                    body["fields"][name] = verified
            body.update(mode=mode.upper(), metrics=result.metrics)
            check_fields(body, self.settings)
            return {"step_id": step_id, "mode": mode, "field": field, "page": page}

        saved = self.repo.update(
            case["id"], case["version"], state["actor"], "agent_fields_extracted", save
        )
        state.update(validated_version=None, reviewed=False, reply=None, knowledge_searched=False)
        summary = self.case_summary(saved)
        summary["usage"] = (result.metrics.get("usage") or {}).get("total_tokens", 0)
        summary["estimated_cost"] = result.metrics.get("estimated_cost")
        return summary

    @staticmethod
    def extraction_applied(case, step_id):
        return any(
            event["action"] == "agent_fields_extracted"
            and event["detail"].get("step_id") == step_id
            for event in case.get("events", [])
        )

    def knowledge_query(self, state, query=""):
        case = self.case(state)
        inputs = case["inputs"] if case else None
        return KnowledgeQuery(
            query=query,
            **(
                {key: inputs[key] for key in ("part_number", "material", "unit", "currency")}
                | {"revision": inputs["drawing_revision"]}
                if inputs
                else {}
            ),
        )

    def search_knowledge(self, state, args, step_id, timeout):
        result = Knowledge(self.settings).search(
            self.knowledge_query(state, args["query"][:200]), state["actor"]
        )
        state["knowledge_searched"] = True
        state["reviewed"] = False
        return {
            "records": [
                {
                    key: item[key]
                    for key in ("source_id", "title", "version", "eligible_for_cost", "reasons")
                }
                for item in result["results"][:8]
            ]
        }

    def lookup_plm_revision(self, state, args, step_id, timeout):
        case = self.case(state)
        number = case["fields"].get("drawing.part_number", {}).get("value") if case else None
        if not number:
            raise DomainError("AGENT_MISSING_FACT", "先取得有来源的零件号。")
        record = self.workflow.systems.plm.read_item_revision(number, "DE-DEMO")
        return {"version": record.version, "revisions": record.payload["revisions"]}

    def lookup_erp_rate(self, state, args, step_id, timeout):
        case = self.case(state)
        material = case["fields"].get("drawing.material", {}).get("value") if case else None
        if not material:
            raise DomainError("AGENT_MISSING_FACT", "先取得有来源的材料。")
        record = self.workflow.systems.erp.read_cost_reference(material, "DE-DEMO")
        return {
            "version": record.version,
            "material": material,
            "rates": {
                k: record.payload[k] for k in ("material_unit", "machine_hourly", "rate_version")
            },
        }

    def run_validation(self, state, args, step_id, timeout):
        case = self.case(state)
        assert case is not None
        check_fields(case, self.settings)
        state["validated_version"] = case["version"] if not case["issues"] else None
        return self.case_summary(case)

    def calculate_cost(self, state, args, step_id, timeout):
        case = self.case(state)
        expected = state["validated_version"]
        if case is None or expected is None or not state["knowledge_searched"]:
            raise DomainError("AGENT_COST_PRECONDITION", "计价前需校验字段并检索受控依据。")
        if self.cost_applied(case, expected):
            return self.quote_summary(case)
        if case["version"] != expected or case["issues"]:
            raise DomainError("AGENT_COST_PRECONDITION", "资料有变化或存在阻断项，请重新校验。")
        quoted = self.workflow.cost(case["id"], expected, state["actor"])
        state.update(reviewed=False, reply=None)
        return self.quote_summary(quoted)

    @staticmethod
    def cost_applied(case, expected):
        return case.get("quote") and any(
            event["action"] == "costed" and event["detail"].get("input_version") == expected
            for event in case["events"]
        )

    def draft_reply(self, state, args, step_id, timeout):
        case = self.case(state)
        if not case or not case["quote"] or state["reviewed"]:
            raise DomainError("AGENT_DRAFT_PRECONDITION", "请先完成计价，已评审的草稿交人工处理。")
        text = args["text"].strip()
        if not text:
            raise DomainError("AGENT_BAD_ARGUMENTS", "草稿内容不能为空。")
        state.update(reply=text, reply_version=case["quote"]["draft_version"])
        return {"saved": True, "text": text}

    @staticmethod
    def draft_questions(state, args, step_id, timeout):
        state["draft_questions"] = args["text"].strip()
        return {"saved": True, "text": state["draft_questions"]}

    def flag_risk(self, state, args, step_id, timeout):
        source = args["source_id"]
        sources = {item["id"] for item in state["sources"]}
        if source and source not in sources:
            alias, _, location = source.partition(":")
            doc = self.document(state, alias)
            source = f"{doc['id']}:{location}" if location else doc["id"]
            if location and source not in sources:
                raise DomainError("AGENT_DOCUMENT_SCOPE", "风险引用不属于当前附件。")
        state["risks"].append(args | {"source_id": source})
        if state["reviewed"]:
            state.update(reviewed=False, review_attention=True)
        return {"risk_count": len(state["risks"])}

    def ask_human(self, state, args, step_id, timeout):
        if args["audience"] not in {"engineer", "customer"} or not args["question"].strip():
            raise DomainError("AGENT_BAD_ARGUMENTS", "请选择复核对象并填写问题。")
        question = args
        case = self.case(state)
        if case and any(issue["code"] == "REVISION_CONFLICT" for issue in case["issues"]):
            question = args | {
                "options": [
                    "补充符合询价要求的已发布图纸",
                    "补充客户正式更新的 RFQ 和对应已发布图纸",
                    "暂停处理",
                ],
            }
        state.update(status="waiting", question=question)
        return question

    def review_package(self, state, args, step_id, timeout):
        case = self.case(state)
        intent = state["intent"]
        if intent == "prepare_quote":
            valid = bool(
                case
                and case["quote"]
                and state["reply"]
                and state["reply_version"] == case["quote"]["draft_version"]
                and not case["issues"]
                and self.workflow.references_match(case, state["actor"])
            )
        elif intent == "review_drawing":
            valid = bool(
                case and state["validated_version"] == case["version"] and not case["issues"]
            )
        else:
            valid = state["knowledge_searched"]
        valid = valid and not state["review_attention"]
        state["reviewed"] = valid
        return {
            "consistent": valid,
            "intent": intent,
            "draft": self.quote_summary(case or {}),
            "reply": state["reply"],
            "risks": state["risks"][-3:],
            "issues": (case or {}).get("issues", [])[:8],
        }

    def finish(self, state, args, step_id, timeout):
        if (
            not state["reviewed"]
            or not self.review_package(state, {}, step_id, timeout)["consistent"]
        ):
            raise DomainError("AGENT_FINISH_PRECONDITION", "交付包尚未通过一致性评审。")
        state.update(status="completed", summary=args["summary"])
        return {"summary": args["summary"], "case_id": state["id"] if self.case(state) else None}
