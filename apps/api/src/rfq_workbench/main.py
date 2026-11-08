"""HTTP application factory, demo authorization, and stable error responses."""

from typing import Annotated

from fastapi import Depends, FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from rfq_workbench.config import Settings
from rfq_workbench.contracts import ApproveRequest, ChangeInputs, ConfirmedInputs, Mutation
from rfq_workbench.errors import DomainError
from rfq_workbench.repository import Repository
from rfq_workbench.service import Workflow


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = Repository(settings.data_dir / "workbench.sqlite3")
    workflow = Workflow(settings, repository)
    app = FastAPI(
        title="PrecisionMotion RFQ Workbench",
        version="0.1.0",
        description="Local synthetic-data internal-budget prototype. Not a production quotation system.",
    )
    app.state.workflow = workflow
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.allowed_origin],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-Demo-Token"],
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
    def list_runs(_who: Annotated[str, Depends(actor)]):
        return repository.list()

    @app.post("/runs/confirmed", status_code=201)
    def confirmed(inputs: ConfirmedInputs, who: Annotated[str, Depends(engineer)]):
        return workflow.create_confirmed(inputs, who)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, _who: Annotated[str, Depends(actor)]):
        return repository.get(run_id)

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
        return workflow.export(run_id, request.expected_version, who)

    return app
