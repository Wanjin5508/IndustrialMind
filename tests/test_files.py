"""Acceptance checks use real fixture bytes, isolated storage and fake network only."""

import json
from dataclasses import replace

import pytest
from conftest import APPROVER, ENGINEER, ROOT, approve
from fastapi.testclient import TestClient

from rfq_workbench.errors import DomainError
from rfq_workbench.extraction import LocalTextProvider, multimodal_input
from rfq_workbench.main import create_app


def fixture_run(client, case="DEV-001"):
    response = client.post(f"/runs/from-fixture/{case}", headers=ENGINEER)
    assert response.status_code == 201, response.text
    return response.json()


def analyze(client, run):
    response = client.post(
        f"/runs/{run['id']}/analyze",
        headers=ENGINEER,
        json={"expected_version": run["version"], "mode": "local_text"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def cost(client, run):
    return client.post(
        f"/runs/{run['id']}/cost", headers=ENGINEER, json={"expected_version": run["version"]}
    )


def test_real_files_to_internal_export_and_evidence(client):
    run = analyze(client, fixture_run(client))
    assert run["state"] == "VALIDATING"
    assert run["inputs"]["quantity"] == 100
    assert run["metrics"]["real_model_call"] is False
    field = run["fields"]["drawing.material"]
    source = next(s for s in run["sources"] if s["id"] == field["source_id"])
    assert source["text"] == "Material: Al-6061-T6"
    assert source["locator"] == "page" and source["bbox"] is None
    path = f"/runs/{run['id']}/documents/{source['document_id']}/pages/1"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=ENGINEER).content.startswith(b"\x89PNG")
    run = cost(client, run).json()
    assert run["quote"]["total_price"] == "2547.00"
    run = approve(client, run)
    response = client.post(
        f"/runs/{run['id']}/export", headers=APPROVER, json={"expected_version": run["version"]}
    )
    assert response.json()["state"] == "EXPORTED_DRAFT"
    assert "NOT A CUSTOMER OFFER" in response.json()["export"]["markdown"]


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("DEV-003", "REVISION_CONFLICT"),
        ("DEV-004", "MISSING_FIELD"),
        ("DEV-005", "UNSUPPORTED_UNIT"),
        ("DEV-006", "UNSUPPORTED_MATERIAL"),
    ],
)
def test_exception_fixtures_block(client, case, code):
    run = analyze(client, fixture_run(client, case))
    assert code in {i["code"] for i in run["issues"]}
    assert run["state"] == "NEEDS_ENGINEERING_REVIEW"
    assert cost(client, run).status_code == 422
    if case == "DEV-004":
        assert run["fields"]["drawing.material"]["value"] is None


def test_replacing_wrong_revision_restores_workflow(client):
    run = analyze(client, fixture_run(client, "DEV-003"))
    old_hash = next(d["sha256"] for d in run["documents"] if d["kind"] == "drawing")
    data = (ROOT / "fixtures/dev/drawings/PM-BRKT-010_B.pdf").read_bytes()
    response = client.post(
        f"/runs/{run['id']}/drawing",
        headers=ENGINEER,
        data={"expected_version": run["version"], "reason": "Received approved B revision"},
        files={"drawing": ("approved-B.pdf", data, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    run = analyze(client, response.json())
    assert run["state"] == "VALIDATING"
    assert next(d["sha256"] for d in run["documents"] if d["kind"] == "drawing") != old_hash
    assert cost(client, run).status_code == 200
    event = next(e for e in run["events"] if e["action"] == "drawing_replaced")
    assert event["detail"]["old_documents"][0]["sha256"] == old_hash


def test_human_quantity_change_preserves_evidence_and_invalidates_approval(client):
    run = approve(client, cost(client, analyze(client, fixture_run(client))).json())
    response = client.patch(
        f"/runs/{run['id']}/fields",
        headers=ENGINEER,
        json={
            "expected_version": run["version"],
            "values": {"rfq.quantity": "500"},
            "reason": "Customer quantity clarification",
        },
    )
    changed = response.json()
    assert changed["approval"] is None and changed["quote"] is None
    field = changed["fields"]["rfq.quantity"]
    assert field["original_value"] == "100" and field["human_confirmed"] is True
    assert (
        next(s for s in changed["sources"] if s["id"] == field["source_id"])["text"]
        == "Quantity: 100"
    )
    changed = cost(client, changed).json()
    assert changed["quote"]["total_price"] == "12255.00"
    assert (
        client.post(
            f"/runs/{run['id']}/export",
            headers=APPROVER,
            json={"expected_version": changed["version"]},
        ).status_code
        == 403
    )


def test_bad_source_and_fake_geometry_cannot_auto_cost(client):
    class BadProvider:
        def extract(self, body):
            result = LocalTextProvider().extract(body)
            result.fields.drawing.material.source_id = "invented"
            result.fields.drawing.width_mm.value = "999"
            return result

    client.app.state.file_workflow.providers["local_text"] = BadProvider()
    run = analyze(client, fixture_run(client))
    assert {"INVALID_SOURCE", "VISUAL_REVIEW"} <= {i["code"] for i in run["issues"]}
    assert cost(client, run).status_code == 422


def test_upload_corrupt_and_oversize_pdf_rejected(client):
    rfq = ("rfq.md", b"RFQ", "text/plain")
    for data in (b"not a PDF", b"%PDF-corrupt", b"x" * (5 * 1024 * 1024 + 1)):
        response = client.post(
            "/runs/upload",
            headers=ENGINEER,
            files={"rfq": rfq, "drawing": ("drawing.pdf", data, "application/pdf")},
        )
        assert response.status_code == 422
    assert client.get("/runs", headers=ENGINEER).json() == []


def test_live_requires_config_and_payload_includes_actual_image(client, settings):
    run = fixture_run(client)
    response = client.post(
        f"/runs/{run['id']}/analyze",
        headers=ENGINEER,
        json={"expected_version": run["version"], "mode": "live"},
    )
    assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
    content = multimodal_input(run, settings.data_dir)[0]["content"]
    assert any(
        c["type"] == "input_image" and c["image_url"].startswith("data:image/png;base64,iVBOR")
        for c in content
    )
    assert "gold" not in json.dumps(content)


def test_model_failure_is_persisted_and_retryable(settings):
    class FailedProvider:
        def extract(self, _body):
            raise DomainError("MODEL_REQUEST_FAILED", "Model timeout", 502)

    with TestClient(
        create_app(replace(settings, model_key="test-only", model_name="fake-model"))
    ) as client:
        client.app.state.file_workflow.providers["live"] = FailedProvider()
        run = fixture_run(client)
        response = client.post(
            f"/runs/{run['id']}/analyze",
            headers=ENGINEER,
            json={"expected_version": run["version"], "mode": "live"},
        )
        assert response.status_code == 502
        run = client.get(f"/runs/{run['id']}", headers=ENGINEER).json()
        assert run["state"] == "FAILED_RETRYABLE" and run["quote"] is None
        assert analyze(client, run)["state"] == "VALIDATING"


def test_runtime_never_reads_answer_directories(client, monkeypatch):
    from pathlib import Path

    original = Path.open

    def guarded(path, *args, **kwargs):
        assert not ({"gold", "authoring", "evals"} & set(path.parts))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert analyze(client, fixture_run(client))["state"] == "VALIDATING"
