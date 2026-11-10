"""Durable API deduplication covers creations, uploads, approvals and request races."""

import sys

import pytest
from conftest import APPROVER, ENGINEER, ROOT, create_and_cost, fixture_rfq

from rfq_workbench.errors import DomainError
from rfq_workbench.main import create_app

sys.path.insert(0, str(ROOT / "scripts"))
from m9_scenarios import scenario  # noqa: E402


def test_sync_request_idempotency(tmp_path):
    result = scenario("M9-16", tmp_path)
    assert result["state"] == "VERIFIED" and result["draft_count"] == 1


@pytest.mark.parametrize(
    "path", ["/tasks", "/runs/confirmed", "/runs/from-fixture/DEV-001", "/runs/upload"]
)
def test_creation_key_preserves_response_across_restart(client, settings, inputs, path):
    headers = ENGINEER | {"Idempotency-Key": "creation-key-123456"}
    body = {"intent": "prepare_quote"} if path == "/tasks" else inputs

    def send(c):
        if path == "/runs/upload":
            return c.post(
                path,
                headers=headers,
                files={
                    "rfq": ("request.md", fixture_rfq().read_bytes()),
                    "drawing": (
                        "drawing.pdf",
                        (ROOT / "fixtures/dev/drawings/PM-BRKT-010_B.pdf").read_bytes(),
                    ),
                },
            )
        return c.post(path, headers=headers, json=body)

    first = send(client)
    assert first.status_code == 201
    from fastapi.testclient import TestClient

    with TestClient(create_app(settings)) as restarted:
        repeated = send(restarted)
        assert repeated.status_code == first.status_code and repeated.json() == first.json()
        assert len(restarted.get("/runs", headers=ENGINEER).json()) == 1


def test_approve_export_retries_return_first_response(client, inputs):
    run = create_and_cost(client, inputs)
    url = f"/runs/{run['id']}"
    body = {
        "expected_version": run["version"],
        "technical_confirmed": True,
        "internal_only": True,
        "note": "Verified",
    }
    header = APPROVER | {"Idempotency-Key": "approval-key-123456"}
    first = client.post(url + "/approve", headers=header, json=body)
    assert first.is_success
    assert client.post(url + "/approve", headers=header, json=body).json() == first.json()
    body = {"expected_version": first.json()["version"]}
    first = client.post(url + "/export", headers=header, json=body)
    assert first.is_success
    assert client.post(url + "/export", headers=header, json=body).json() == first.json()


def test_in_progress_and_payload_reuse_and_key_validation(client):
    service = client.app.state.idempotency
    assert service.reserve("scope", "parallel-key-12345", "hash") is None
    with pytest.raises(DomainError) as error:
        service.reserve("scope", "parallel-key-12345", "hash")
    assert error.value.code == "IDEMPOTENCY_IN_PROGRESS"
    with pytest.raises(DomainError) as error:
        service.reserve("scope", "parallel-key-12345", "other")
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
    response = client.post("/tasks", json={}, headers=ENGINEER | {"Idempotency-Key": "short"})
    assert response.status_code == 422


def test_invalid_upload_replays_validation_response_instead_of_stalling(client):
    headers = ENGINEER | {"Idempotency-Key": "invalid-upload-123456"}
    files = {"rfq": ("request.txt", b"Part number: PM-BRKT-010")}
    first = client.post("/runs/upload", headers=headers, files=files)
    second = client.post("/runs/upload", headers=headers, files=files)
    assert first.status_code == second.status_code == 422
    assert first.json() == second.json()
    assert client.get("/runs", headers=ENGINEER).json() == []
