"""Milestone acceptance: bounded failure, stale workers and read-only previews."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from conftest import APPROVER, ENGINEER, approve, create_and_cost, download_draft
from fastapi.testclient import TestClient

from rfq_workbench import file_workflow
from rfq_workbench.errors import DomainError
from rfq_workbench.main import create_app


def create_task(client, case="DEV-001"):
    response = client.post(
        "/tasks", headers=ENGINEER, json={"intent": "prepare_quote", "fixture_id": case}
    )
    assert response.status_code == 201, response.text
    return response.json()


def resume(client, run, mode):
    return client.post(
        f"/tasks/{run['id']}/resume",
        headers=ENGINEER,
        json={"expected_version": run["version"], "mode": mode},
    )


def get_run(client, run):
    return client.get(f"/runs/{run['id']}", headers=ENGINEER).json()


def test_timeout_budget_survives_restart_and_fallback(settings):
    settings = replace(settings, simulate_model_timeout=True)
    with TestClient(create_app(settings)) as client:
        run = create_task(client)
        assert resume(client, run, "live").status_code == 504
        run = get_run(client, run)
        assert run["state"] == "FAILED_RETRYABLE"
        assert run["metrics"]["provider"] == "TIMEOUT_SIMULATION"
        assert run["metrics"]["real_model_call"] is False

    with TestClient(create_app(settings)) as client:
        for number in (2, 3):
            assert resume(client, run, "live").json()["error"]["code"] == "MODEL_TIMEOUT"
            run = get_run(client, run)
            assert run["extraction_attempt"]["number"] == number
        assert run["state"] == "FAILED_FINAL"
        denied = resume(client, run, "live")
        assert denied.status_code == 409 and denied.json()["error"]["code"] == "RETRY_LIMIT"
        assert get_run(client, run) == run
        assert run["quote"] is None and run["approval"] is None
        failed = [e["detail"] for e in run["events"] if e["action"] == "extraction_failed"]
        assert [e["number"] for e in failed] == [1, 2, 3]
        assert all(e["output_version"] == e["input_version"] + 2 for e in failed)
        run = resume(client, run, "replay").json()
        assert run["state"] == "READY_FOR_APPROVAL"
        rejected = client.post(
            f"/runs/{run['id']}/analyze",
            headers=ENGINEER,
            json={"expected_version": run["version"], "mode": "live"},
        )
        assert rejected.json()["error"]["code"] == "RETRY_LIMIT"
        assert get_run(client, run)["quote"] == run["quote"]


def test_interruption_recovery_rejects_late_result(settings, monkeypatch):
    class Clock(datetime):
        current = datetime.now(UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(file_workflow, "datetime", Clock)
    first_app = create_app(settings)
    files = first_app.state.file_workflow
    original_provider = files.providers["local_text"]
    started, release = Event(), Event()

    class StalledProvider:
        def extract(self, body):
            started.set()
            assert release.wait(10)
            return original_provider.extract(body)

    files.providers["local_text"] = StalledProvider()
    with TestClient(first_app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        run = create_task(client)
        waiting = pool.submit(
            files.analyze, run["id"], run["version"], "local_text", "demo-engineer"
        )
        try:
            assert started.wait(5)
            run = get_run(client, run)
            path = f"/runs/{run['id']}/recover"
            request = {"expected_version": run["version"]}
            assert run["state"] == "EXTRACTING"
            assert client.post(path, headers=ENGINEER, json=request).status_code == 409
            assert client.post(path, headers=APPROVER, json=request).status_code == 403
            Clock.current += timedelta(seconds=files.lease_seconds() + 1)
            with TestClient(create_app(settings)) as restarted:
                response = restarted.post(path, headers=ENGINEER, json=request)
                assert response.status_code == 200, response.text
                recovered = response.json()
                assert recovered["state"] == "FAILED_RETRYABLE"
                assert recovered["issues"][0]["code"] == "EXTRACTION_INTERRUPTED"
                complete = resume(restarted, recovered, "local_text").json()
                assert complete["state"] == "READY_FOR_APPROVAL"
                assert complete["extraction_attempt"]["number"] == 2
        finally:
            release.set()
        with pytest.raises(DomainError) as error:
            waiting.result(timeout=5)
        assert error.value.code == "VERSION_CONFLICT"
        assert get_run(client, run) == complete


def test_replay_binds_to_file_bytes_and_records_provenance(client):
    mismatch = create_task(client, "DEV-002")
    assert resume(client, mismatch, "replay").json()["error"]["code"] == "REPLAY_UNAVAILABLE"
    assert get_run(client, mismatch) == mismatch
    run = resume(client, create_task(client), "replay").json()
    assert run["mode"] == "REPLAY" and run["metrics"]["real_model_call"] is False
    assert run["metrics"]["replay_source"]["id"] == "DEV-001-M3-LIVE"
    assert run["quote"]["total_price"] == "2547.00"
    fact = run["fields"]["drawing.material"]
    assert fact["evidence"]["status"] == "TEXT_VERIFIED"
    assert fact["source_id"].startswith(
        next(d["id"] for d in run["documents"] if d["kind"] == "drawing")
    )
    for action in ("extracted", "costed"):
        detail = next(e["detail"] for e in run["events"] if e["action"] == action)
        assert (
            len(detail["output_hash"]) == 64 and detail["output_version"] > detail["input_version"]
        )


def test_integration_preview_is_read_only_and_hides_stale_erp(client, settings, inputs):
    run = create_and_cost(client, inputs)
    path = f"/runs/{run['id']}/integration-preview"
    assert client.get(path).status_code == 401
    preview = client.get(path, headers=ENGINEER).json()
    assert preview["mode"] == "SIMULATED"
    assert preview["plm"]["released_revision"] == "B"
    assert preview["erp"]["total_price"] == "2547.00"
    assert preview["erp"]["approval_status"] == "PENDING"
    assert preview["mes"]["observed_minutes_per_part"] == "8.1"
    assert client.get(path, headers=ENGINEER).json() == preview
    assert get_run(client, run) == run
    run = approve(client, run)
    approved = client.get(path, headers=APPROVER).json()
    assert approved["erp"]["preview_id"] == preview["erp"]["preview_id"]
    assert approved["erp"]["approval_status"] == "APPROVED"
    ref = json.loads(settings.reference_path.read_text("utf-8"))
    ref["rate_version"] += "-changed"
    settings.reference_path.write_text(json.dumps(ref), encoding="utf-8")
    stale = client.get(path, headers=APPROVER).json()
    assert stale["erp_status"] == "STALE_DRAFT" and stale["erp"] is None
    assert get_run(client, run) == run


def test_integration_boundary_and_revision_conflict(client):
    query = client.post("/tasks", headers=APPROVER, json={"intent": "search_knowledge"}).json()
    private_path = f"/runs/{query['id']}/integration-preview"
    assert client.get(private_path, headers=ENGINEER).status_code == 404
    assert client.get(private_path, headers=APPROVER).status_code == 403
    run = resume(client, create_task(client, "DEV-003"), "local_text").json()
    assert run["state"] == "NEEDS_ENGINEERING_REVIEW"
    preview = client.get(f"/runs/{run['id']}/integration-preview", headers=ENGINEER).json()
    assert preview["plm"]["drawing_revision"] == "A" and preview["plm"]["released_revision"] == "B"
    assert preview["erp_status"] == "NO_DRAFT" and preview["erp"] is None


def test_retry_and_replay_cannot_duplicate_approval_or_export(client):
    run = resume(client, create_task(client), "replay").json()
    run = approve(client, run)
    assert resume(client, run, "replay").json() == run
    path = f"/runs/{run['id']}/export"
    exported = client.post(path, headers=APPROVER, json={"expected_version": run["version"]}).json()
    assert exported["state"] == "EXPORTED_DRAFT"
    assert "REPLAY / DEV-001-M3-LIVE" in download_draft(client, exported).text
    assert (
        client.post(path, headers=APPROVER, json={"expected_version": run["version"]}).status_code
        == 409
    )
    assert (
        client.post(path, headers=APPROVER, json={"expected_version": exported["version"]}).json()
        == exported
    )
    events = [e["action"] for e in exported["events"]]
    assert events.count("approved") == events.count("exported") == 1
