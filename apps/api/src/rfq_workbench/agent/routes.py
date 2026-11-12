"""HTTP entry points for the optional RFQ agent."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from rfq_workbench.agent.runner import MAX_ATTACHMENTS, AgentRunner
from rfq_workbench.errors import DomainError


def mount_agent(app, workflow, files, engineer, read_upload):
    runner = AgentRunner(workflow, files)
    app.state.agent_runner = runner
    router = APIRouter(prefix="/agent/runs", tags=["RFQ Agent"])
    engineer_actor = Depends(engineer)

    def inputs(attachments: list[UploadFile] | None) -> list:
        if attachments and len(attachments) > MAX_ATTACHMENTS:
            raise DomainError("AGENT_ATTACHMENT_LIMIT", "每个任务最多六份附件。")
        return [
            (item.filename or "attachment.txt", read_upload(item)) for item in attachments or []
        ]

    @router.post("", status_code=201)
    def create(
        actor: str = engineer_actor,
        request: Annotated[str, Form(max_length=2500)] = "",
        attachments: Annotated[list[UploadFile] | None, File()] = None,
    ):
        return runner.create(request, inputs(attachments), actor)

    @router.get("/{run_id}")
    def get(run_id: str, actor: str = engineer_actor):
        return runner.view(runner.store.get(run_id, actor))

    @router.post("/{run_id}/step")
    def step(run_id: str, actor: str = engineer_actor):
        return runner.step(run_id, actor)

    @router.post("/{run_id}/retry")
    def retry(run_id: str, actor: str = engineer_actor):
        return runner.retry(run_id, actor)

    @router.post("/{run_id}/answer")
    def answer(
        run_id: str,
        actor: str = engineer_actor,
        answer: Annotated[str, Form(max_length=2500)] = "",
        attachments: Annotated[list[UploadFile] | None, File()] = None,
    ):
        return runner.answer(run_id, actor, answer, inputs(attachments))

    @router.post("/{run_id}/stop")
    def stop(run_id: str, actor: str = engineer_actor):
        return runner.stop(run_id, actor)

    app.include_router(router)
