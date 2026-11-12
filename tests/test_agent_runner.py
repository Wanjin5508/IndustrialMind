"""Agent boundaries and crash recovery at the business-write checkpoint."""

from dataclasses import replace
from time import time

import pytest
from conftest import APPROVER, ENGINEER, ROOT, fixture_rfq
from fastapi.testclient import TestClient

from rfq_workbench.agent.model import Call
from rfq_workbench.errors import DomainError
from rfq_workbench.integrations.faults import SimulatedCrash
from rfq_workbench.main import create_app


def test_agent_switch_and_engineering_access(client, settings):
    assert client.post("/agent/runs", headers=ENGINEER, data={"request": "报价"}).status_code == 404
    with TestClient(create_app(replace(settings, agent_enabled=True))) as enabled:
        assert enabled.get("/capabilities", headers=ENGINEER).json()["agent_enabled"] is True
        denied = enabled.post("/agent/runs", headers=APPROVER, data={"request": "报价"})
        assert denied.status_code == 403
        created = enabled.post("/agent/runs", headers=ENGINEER, data={"request": "报价"})
        assert created.status_code == 201
        path = f"/agent/runs/{created.json()['id']}"
        assert enabled.post(path + "/stop", headers=ENGINEER).json()["status"] == "stopped"
        assert enabled.post(path + "/step", headers=ENGINEER).json()["steps"] == []


def test_model_connection_pause_retries_the_same_run(settings, monkeypatch):
    app = create_app(replace(settings, agent_enabled=True))
    runner = app.state.agent_runner
    decisions = iter(
        [
            None,
            None,
            None,
            Call(
                "ask_human",
                {
                    "audience": "customer",
                    "question": "请补充已发布图纸。",
                    "options": [],
                },
                tokens=100,
            ),
        ]
    )

    def decide(*args):
        call = next(decisions)
        if call is None:
            raise DomainError("AGENT_MODEL_TIMEOUT", "模型服务连接失败或超时。")
        return call

    monkeypatch.setattr(runner.model, "decide", decide)
    with TestClient(app) as client:
        created = client.post(
            "/agent/runs",
            headers=ENGINEER,
            data={"request": "报价"},
            files={"attachments": ("request.txt", b"RFQ 100 parts", "text/plain")},
        ).json()
        path = f"/agent/runs/{created['id']}"

        def step():
            return client.post(path + "/step", headers=ENGINEER).json()

        assert step()["status"] == "ready"
        paused = step()
        assert paused["status"] == "model_paused"
        assert paused["service_issue"]["code"] == "AGENT_MODEL_TIMEOUT"
        assert paused["question"] is None
        assert step()["usage"] == paused["usage"]
        assert client.post(path + "/retry", headers=APPROVER).status_code == 403
        assert (
            client.post(path + "/answer", headers=ENGINEER, data={"answer": "继续"}).status_code
            == 409
        )

        resumed = client.post(path + "/retry", headers=ENGINEER).json()
        assert resumed["status"] == "ready" and resumed["service_issue"] is None
        assert resumed["documents"] == paused["documents"] == created["documents"]
        assert resumed["usage"] == paused["usage"]
        assert step()["status"] == "ready"
        waiting = step()
        assert waiting["status"] == "waiting" and waiting["service_issue"] is None
        assert waiting["question"]["audience"] == "customer" and waiting["answers"] == []
        assert client.post(path + "/retry", headers=ENGINEER).status_code == 409


def test_saved_model_error_can_resume_without_a_customer_answer(settings):
    app = create_app(replace(settings, agent_enabled=True))
    runner = app.state.agent_runner
    actor = "demo-engineer"
    created = runner.create("报价", [], actor)
    run_id = created["id"]
    runner.store.update(
        run_id, actor, lambda body: runner.ask(body, "连接失败", "AGENT_MODEL_TIMEOUT")
    )

    with TestClient(app) as client:
        path = f"/agent/runs/{run_id}"
        paused = client.get(path, headers=ENGINEER).json()
        assert paused["status"] == "model_paused" and paused["question"] is None
        resumed = client.post(path + "/retry", headers=ENGINEER).json()
        assert resumed["status"] == "ready" and resumed["answers"] == []


def test_recovery_does_not_repeat_committed_extraction(settings, monkeypatch):
    settings = replace(settings, agent_enabled=True)
    app = create_app(settings)
    runner = app.state.agent_runner
    actor = "demo-engineer"
    rfq = fixture_rfq()
    drawing = ROOT / "fixtures/dev/drawings/PM-BRKT-010_B.pdf"
    run = runner.create("准备内部报价", [(p.name, p.read_bytes()) for p in (rfq, drawing)], actor)
    rfq_id, drawing_id = [doc["id"] for doc in run["documents"]]
    calls = iter(
        [
            Call(
                "classify_attachments",
                {
                    "intent": "prepare_quote",
                    "rfq_id": rfq_id,
                    "drawing_id": drawing_id,
                    "plan": ["核对附件"],
                },
            ),
            Call(
                "extract_fields", {"mode": "local_text", "document_id": "", "page": 0, "field": ""}
            ),
        ]
    )
    monkeypatch.setattr(runner.model, "decide", lambda *args: next(calls))
    run_id = run["id"]
    assert runner.step(run_id, actor)["status"] == "ready"
    execute = runner.gateway.execute

    def crash_after_write(*args):
        execute(*args)
        raise SimulatedCrash()

    monkeypatch.setattr(runner.gateway, "execute", crash_after_write)
    with pytest.raises(SimulatedCrash):
        runner.step(run_id, actor)
    before = runner.gateway.repo.get(run_id)
    assert before["fields"]["rfq.quantity"]["value"] == "100"
    assert runner.store.get(run_id, actor)["pending"]["call"]["name"] == "extract_fields"

    def expire(body):
        body["lease"]["until"] = time() - 1

    runner.store.update(run_id, actor, expire)
    restarted = create_app(settings).state.agent_runner
    resumed = restarted.step(run_id, actor)
    assert resumed["status"] == "ready" and resumed["usage"]["steps"] == 2
    assert resumed["steps"][-1]["call"]["name"] == "extract_fields"
    assert resumed["steps"][-1]["error"] is None
    assert restarted.gateway.repo.get(run_id)["version"] == before["version"]
