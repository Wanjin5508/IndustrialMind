"""Optional API request deduplication with durable in-progress reservations."""

import hashlib
import json
import re
from datetime import timedelta

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from rfq_workbench.canonical import canonical_hash
from rfq_workbench.contracts import ApproveRequest, ConfirmedInputs, Mutation, TaskRequest
from rfq_workbench.errors import DomainError
from rfq_workbench.integrations.contracts import SyncRequest

REQUEST_MODELS = {
    "/tasks": TaskRequest,
    "/runs/confirmed": ConfirmedInputs,
    "/runs/{run_id}/approve": ApproveRequest,
    "/runs/{run_id}/export": Mutation,
    "/runs/{run_id}/sync": SyncRequest,
}
SUPPORTED = set(REQUEST_MODELS) | {"/runs/upload", "/runs/from-fixture/{case_id}"}


class Idempotency:
    def __init__(self, repo, clock):
        self.repo, self.clock = repo, clock

    def reserve(self, scope: str, key: str, digest: str) -> Response | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
            raise DomainError(
                "IDEMPOTENCY_KEY_INVALID", "幂等键须为 16–128 位字母、数字、横线或下划线。"
            )
        now = self.clock.now()
        with self.repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM idempotency_records WHERE scope=? AND key=?", (scope, key)
            ).fetchone()
            if row and row["expires_at"] <= now.isoformat():
                conn.execute(
                    "DELETE FROM idempotency_records WHERE scope=? AND key=?", (scope, key)
                )
                row = None
            if row:
                if row["request_sha256"] != digest:
                    raise DomainError("IDEMPOTENCY_KEY_REUSED", "该幂等键已用于不同请求。")
                if row["status"] is None:
                    raise DomainError(
                        "IDEMPOTENCY_IN_PROGRESS", "首次请求仍待确认，请查询任务状态。", 409
                    )
                return Response(
                    content=row["response"],
                    status_code=row["status"],
                    media_type="application/json",
                )
            conn.execute(
                "INSERT INTO idempotency_records VALUES(?,?,?,NULL,NULL,?,?)",
                (scope, key, digest, now.isoformat(), (now + timedelta(hours=24)).isoformat()),
            )
        return None

    def finish(self, scope: str, key: str, response: Response) -> None:
        with self.repo.connect() as conn:
            conn.execute(
                "UPDATE idempotency_records SET status=?,response=? WHERE scope=? AND key=?",
                (response.status_code, response.body.decode("utf-8"), scope, key),
            )


class IdempotentRoute(APIRoute):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.path in SUPPORTED and "POST" in self.methods:
            self.openapi_extra = self.openapi_extra or {}
            if self.path != "/runs/{run_id}/sync":
                self.openapi_extra["parameters"] = [
                    {
                        "name": "Idempotency-Key",
                        "in": "header",
                        "required": False,
                        "schema": {
                            "type": "string",
                            "minLength": 16,
                            "maxLength": 128,
                            "pattern": "^[A-Za-z0-9_-]+$",
                        },
                    }
                ]

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            key = request.headers.get("Idempotency-Key")
            if not key or self.path not in SUPPORTED or request.method != "POST":
                return await original(request)
            settings = request.app.state.settings
            token = request.headers.get("X-Demo-Token")
            actors = {
                settings.engineer_token: "demo-engineer",
                settings.approver_token: "demo-approver",
            }
            if token not in actors:
                raise DomainError("UNAUTHORIZED", "A valid local demo token is required.", 401)
            content = {}
            if self.path == "/runs/upload":
                form = await request.form()
                for name, value in form.multi_items():
                    if isinstance(value, UploadFile):
                        content[name] = {
                            "filename": value.filename,
                            "sha256": hashlib.sha256(await value.read()).hexdigest(),
                        }
                        await value.seek(0)
                    else:
                        content[name] = value
            elif model := REQUEST_MODELS.get(self.path):
                try:
                    content = model.model_validate(await request.json()).model_dump(mode="json")
                except (ValidationError, json.JSONDecodeError):
                    return await original(request)
            service = request.app.state.idempotency
            scope = canonical_hash({"actor": actors[token], "method": "POST", "path": self.path})
            digest = canonical_hash({"path": request.url.path, "content": content})
            cached = service.reserve(scope, key, digest)
            if cached is not None:
                return cached
            try:
                response = await original(request)
            except DomainError as exc:
                response = JSONResponse(
                    status_code=exc.status,
                    content={"error": {"code": exc.code, "message": exc.message}},
                )
            service.finish(scope, key, response)
            return response

        return handle
