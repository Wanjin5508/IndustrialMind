"""HTTP application factory, demo authorization, and stable error responses."""

from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, Header, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from rfq_workbench.agent.routes import mount_agent
from rfq_workbench.config import Settings
from rfq_workbench.contracts import (
    AnalyzeRequest,
    ApproveRequest,
    ChangeInputs,
    ConfirmedInputs,
    CorrectFields,
    KnowledgeQuery,
    Mutation,
    ResumeTask,
    TaskRequest,
)
from rfq_workbench.documents import MAX_FILE_BYTES, asset_path
from rfq_workbench.errors import DomainError
from rfq_workbench.file_workflow import FileWorkflow, fixture_catalog
from rfq_workbench.idempotency import Idempotency, IdempotentRoute
from rfq_workbench.integrations import IntegrationPreview, preview_integrations
from rfq_workbench.integrations.contracts import (
    DispatchDue,
    ResolveSync,
    SyncIntentView,
    SyncRequest,
)
from rfq_workbench.integrations.ports import HttpError, TransportError
from rfq_workbench.knowledge import Knowledge
from rfq_workbench.orchestrator import Orchestrator, visible
from rfq_workbench.repository import Repository
from rfq_workbench.service import Workflow, run_snapshot
from rfq_workbench.sync import SyncService


def create_app(
    settings: Settings | None = None, *, clock=None, systems=None, failpoints=None
) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = Repository(settings.data_dir / "workbench.sqlite3", clock=clock)
    workflow = Workflow(settings, repository, clock=clock, systems=systems)
    sync = SyncService(workflow, failpoints=failpoints)
    file_workflow = FileWorkflow(settings, repository)
    orchestrator = Orchestrator(workflow, file_workflow)
    app = FastAPI(
        title="PrecisionMotion RFQ Workbench",
        version="0.1.0",
        description="Local synthetic-data internal-budget prototype. Not a production quotation system.",
    )
    app.state.workflow = workflow
    app.state.settings = settings
    app.state.file_workflow = file_workflow
    app.state.sync = sync
    app.state.systems = workflow.systems
    app.state.idempotency = Idempotency(repository, workflow.clock)
    app.router.route_class = IdempotentRoute
    knowledge = Knowledge(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.allowed_origin],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-Demo-Token", "Idempotency-Key"],
    )

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
        )

    def actor(x_demo_token: Annotated[str | None, Header()] = None) -> str:
        if x_demo_token == settings.engineer_token:
            return "demo-engineer"
        if x_demo_token == settings.approver_token:
            return "demo-approver"
        raise DomainError("UNAUTHORIZED", "A valid local demo token is required.", 401)

    def engineer(who: Annotated[str, Depends(actor)]) -> str:
        if who != "demo-engineer":
            raise DomainError("FORBIDDEN", "Engineering role is required.", 403)
        return who

    def approver(who: Annotated[str, Depends(actor)]) -> str:
        if who != "demo-approver":
            raise DomainError("FORBIDDEN", "Approval role is required.", 403)
        return who

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "scope": "LOCAL_SYNTHETIC_DEMO"}

    @app.get("/runs")
    def list_runs(who: Annotated[str, Depends(actor)]):
        return [run_snapshot(body) for body in repository.list() if visible(body, who)]

    @app.post("/tasks", status_code=201)
    def create_task(request: TaskRequest, who: Annotated[str, Depends(actor)]):
        return orchestrator.create(request, who)

    @app.post("/tasks/{run_id}/resume")
    def resume_task(run_id: str, request: ResumeTask, who: Annotated[str, Depends(actor)]):
        return run_snapshot(orchestrator.resume(run_id, request, who))

    @app.get("/capabilities")
    def capabilities(_who: Annotated[str, Depends(actor)]):
        return {
            "live_available": settings.simulate_model_timeout
            or bool(settings.model_key and settings.model_name),
            "simulate_model_timeout": settings.simulate_model_timeout,
            "agent_enabled": settings.agent_enabled,
            "model": settings.model_name or None,
            "local_mode": "LOCAL_TEXT",
            "scope": "PM-DEMO / DE-DEMO / PM-BRKT-010",
            "max_file_mib": 5,
            "max_pdf_pages": 3,
        }

    @app.get("/fixtures")
    def fixtures(_who: Annotated[str, Depends(actor)]):
        return [
            {"id": c["id"], "label": c["label"], "origin_type": c["origin_type"]}
            for c in fixture_catalog()
        ]

    @app.post("/knowledge/search")
    def search_knowledge(query: KnowledgeQuery, who: Annotated[str, Depends(actor)]):
        return knowledge.search(query, who)

    @app.get("/knowledge/{record_id}")
    def knowledge_detail(record_id: str, who: Annotated[str, Depends(actor)]):
        return knowledge.get(record_id, KnowledgeQuery(), who)

    @app.post("/runs/from-fixture/{case_id}", status_code=201)
    def from_fixture(case_id: str, who: Annotated[str, Depends(engineer)]):
        return file_workflow.from_fixture(case_id, who)

    def read_upload(upload: UploadFile) -> bytes:
        try:
            return upload.file.read(MAX_FILE_BYTES + 1)
        finally:
            upload.file.close()

    if settings.agent_enabled:
        mount_agent(app, workflow, file_workflow, engineer, read_upload)

    @app.post("/tasks/{run_id}/documents")
    def attach_task_documents(
        run_id: str,
        who: Annotated[str, Depends(engineer)],
        expected_version: Annotated[int, Form(ge=1)],
        rfq: Annotated[UploadFile | None, File()] = None,
        drawing: Annotated[UploadFile | None, File()] = None,
    ):
        uploads = [
            (kind, upload.filename or kind, read_upload(upload))
            for kind, upload in [("rfq", rfq), ("drawing", drawing)]
            if upload
        ]
        return orchestrator.attach(run_id, expected_version, uploads, who)

    @app.post("/runs/upload", status_code=201)
    def upload(
        who: Annotated[str, Depends(engineer)],
        rfq: Annotated[UploadFile, File()],
        drawing: Annotated[UploadFile, File()],
    ):
        return file_workflow.create(
            rfq.filename or "rfq.md",
            read_upload(rfq),
            drawing.filename or "drawing.pdf",
            read_upload(drawing),
            who,
        )

    @app.post("/runs/confirmed", status_code=201)
    def confirmed(inputs: ConfirmedInputs, who: Annotated[str, Depends(engineer)]):
        return workflow.create_confirmed(inputs, who)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, who: Annotated[str, Depends(actor)]):
        return run_snapshot(orchestrator.get(run_id, who))

    @app.get("/runs/{run_id}/integration-preview", response_model=IntegrationPreview)
    def integration_preview(run_id: str, who: Annotated[str, Depends(actor)]):
        try:
            return preview_integrations(orchestrator.get(run_id, who), who, workflow)
        except (HttpError, TransportError) as exc:
            raise DomainError(
                "SNAPSHOT_UNAVAILABLE", "模拟来源当前不可用，请稍后重试。", 409
            ) from exc

    @app.post("/runs/{run_id}/sync", response_model=SyncIntentView)
    def request_sync(
        run_id: str,
        request: SyncRequest,
        who: Annotated[str, Depends(approver)],
        idempotency_key: Annotated[str, Header()] = ...,
    ):
        return sync.request_sync(run_id, request.expected_version, who, request.note)

    @app.get("/runs/{run_id}/sync")
    def list_sync(run_id: str, who: Annotated[str, Depends(actor)]):
        body = orchestrator.get(run_id, who)
        return {
            "mode": "SIMULATED",
            "notice": "NO EXTERNAL WRITE",
            "intents": sync.store.summary(run_id),
            "snapshots": [
                workflow.snapshots.get(identifier).model_dump(mode="json")
                for identifier in body.get("snapshot_refs", {}).get("ids", [])
            ],
        }

    def sync_visible(identifier: str, who: str):
        intent = sync.store.get(identifier)
        orchestrator.get(intent.run_id, who)
        return intent

    @app.post("/sync/dispatch-due")
    def dispatch_due(request: DispatchDue, who: Annotated[str, Depends(approver)]):
        return sync.reconcile_sweep(request.limit, who)

    @app.post("/sync/{intent_id}/dispatch")
    def dispatch_sync(intent_id: str, who: Annotated[str, Depends(approver)]):
        sync_visible(intent_id, who)
        return sync.dispatch_once(intent_id, who)

    @app.post("/sync/{intent_id}/reconcile")
    def reconcile_sync(intent_id: str, who: Annotated[str, Depends(approver)]):
        sync_visible(intent_id, who)
        return sync.reconcile(intent_id, who)

    @app.post("/sync/{intent_id}/resolve")
    def resolve_sync(intent_id: str, request: ResolveSync, who: Annotated[str, Depends(approver)]):
        sync_visible(intent_id, who)
        return sync.resolve(intent_id, request.decision, request.note, who)

    @app.post("/runs/{run_id}/analyze")
    def analyze(run_id: str, request: AnalyzeRequest, who: Annotated[str, Depends(engineer)]):
        return file_workflow.analyze(run_id, request.expected_version, request.mode, who)

    @app.post("/runs/{run_id}/recover")
    def recover(run_id: str, request: Mutation, who: Annotated[str, Depends(engineer)]):
        return file_workflow.recover(run_id, request.expected_version, who)

    @app.patch("/runs/{run_id}/fields")
    def correct_fields(run_id: str, request: CorrectFields, who: Annotated[str, Depends(engineer)]):
        return file_workflow.correct(run_id, request, who)

    @app.post("/runs/{run_id}/drawing")
    def replace_drawing(
        run_id: str,
        who: Annotated[str, Depends(engineer)],
        drawing: Annotated[UploadFile, File()],
        expected_version: Annotated[int, Form(ge=1)],
        reason: Annotated[str, Form(min_length=3, max_length=1000)],
    ):
        return file_workflow.replace_drawing(
            run_id,
            expected_version,
            drawing.filename or "drawing.pdf",
            read_upload(drawing),
            reason,
            who,
        )

    @app.get("/runs/{run_id}/documents/{document_id}/pages/{page}")
    def preview(run_id: str, document_id: str, page: int, _who: Annotated[str, Depends(actor)]):
        return FileResponse(
            asset_path(settings.data_dir, repository.get(run_id), document_id, page),
            media_type="image/png",
            headers={"Cache-Control": "private, no-store"},
        )

    @app.patch("/runs/{run_id}/inputs")
    def change_inputs(run_id: str, request: ChangeInputs, who: Annotated[str, Depends(engineer)]):
        return workflow.change_inputs(
            run_id, request.expected_version, request.inputs, request.reason, who
        )

    @app.post("/runs/{run_id}/cost")
    def cost(run_id: str, request: Mutation, who: Annotated[str, Depends(engineer)]):
        return workflow.cost(run_id, request.expected_version, who)

    @app.post("/runs/{run_id}/approve")
    def approve(run_id: str, request: ApproveRequest, who: Annotated[str, Depends(approver)]):
        return workflow.approve(run_id, request, who)

    @app.post("/runs/{run_id}/export")
    def export(run_id: str, request: Mutation, who: Annotated[str, Depends(approver)]):
        return run_snapshot(workflow.export(run_id, request.expected_version, who))

    @app.get(
        "/runs/{run_id}/export/download",
        response_class=Response,
        responses={200: {"content": {"text/markdown": {}, "application/json": {}}}},
    )
    def download_export(
        run_id: str,
        expected_version: Annotated[int, Query(ge=1)],
        who: Annotated[str, Depends(approver)],
        format: Literal["md", "json"] = "md",
    ):
        body = workflow.download_snapshot(run_id, expected_version, who)
        export = body["export"]
        filename = export["filename"].removesuffix(".md") + f".{format}"
        headers = {
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        if format == "md":
            return Response(export["markdown"], media_type="text/markdown", headers=headers)
        return JSONResponse(
            {
                "kind": "INTERNAL_DRAFT",
                "run_id": run_id,
                "inputs": body["inputs"],
                "quote": export["quote"],
                "approval": export["approval"],
                "extraction_mode": body["mode"],
                "replay_source": body["metrics"].get("replay_source"),
            },
            headers=headers,
        )

    return app
