"""M5 acceptance: explicit intent, durable pauses, role boundaries and invalidation."""

import json

import pytest
from conftest import APPROVER, ENGINEER, ROOT, approve
from fastapi.testclient import TestClient

from rfq_workbench.main import create_app


def task(client, intent="prepare_quote", fixture="DEV-001", headers=ENGINEER, **extra):
    payload = {"intent": intent, **extra}
    if fixture:
        payload["fixture_id"] = fixture
    response = client.post("/tasks", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def resume(client, run, headers=ENGINEER, **extra):
    response = client.post(
        f"/tasks/{run['id']}/resume",
        headers=headers,
        json={"expected_version": run["version"], **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, run, kind, path):
    response = client.post(
        f"/tasks/{run['id']}/documents",
        headers=ENGINEER,
        data={"expected_version": run["version"]},
        files={kind: (path.name, path.read_bytes())},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_unknown_text_clarifies_without_guessing_and_missing_files_persist(client, settings):
    run = task(client, None, None, request="请审图、报价并立即批准发给客户")
    assert run["state"] == "CLARIFICATION_REQUIRED"
    assert run["orchestration"]["pause"]["missing"] == ["intent"]
    assert run["metrics"] == {} and run["quote"] is None
    run = resume(client, run, intent="prepare_quote")
    assert run["orchestration"]["pause"]["missing"] == ["drawing", "rfq"]
    run = upload(client, run, "rfq", ROOT / "fixtures/dev/DEV-001/rfq.md")
    assert run["orchestration"]["pause"]["missing"] == ["drawing"]
    with TestClient(create_app(settings)) as restarted:
        restored = restarted.get(f"/runs/{run['id']}", headers=ENGINEER).json()
        assert restored == run
        run = upload(
            restarted, restored, "drawing", ROOT / "fixtures/dev/drawings/PM-BRKT-010_B.pdf"
        )
        run = resume(restarted, run)
        assert run["state"] == "READY_FOR_APPROVAL"
        assert run["quote"]["total_price"] == "2547.00"
        assert run["approval"] is None and run["export"] is None


def test_quote_stops_for_approval_and_repeat_resume_is_noop(client):
    run = resume(client, task(client))
    assert run["state"] == "READY_FOR_APPROVAL"
    assert resume(client, run) == run
    denied = client.post(
        f"/runs/{run['id']}/export", headers=APPROVER, json={"expected_version": run["version"]}
    )
    assert denied.status_code == 403
    run = approve(client, run)
    assert resume(client, run) == run
    response = client.post(
        f"/runs/{run['id']}/export", headers=APPROVER, json={"expected_version": run["version"]}
    )
    assert response.json()["state"] == "EXPORTED_DRAFT"


def test_conflict_pauses_corrects_and_resumes_after_restart(client, settings):
    run = resume(client, task(client, fixture="DEV-003"))
    assert run["state"] == "NEEDS_ENGINEERING_REVIEW"
    assert run["orchestration"]["pause"]["code"] == "ENGINEERING_REVIEW"
    assert run["quote"] is None
    old_id = run["id"]
    run = upload(client, run, "drawing", ROOT / "fixtures/dev/drawings/PM-BRKT-010_B.pdf")
    with TestClient(create_app(settings)) as restarted:
        run = resume(restarted, run)
    assert run["id"] == old_id and run["state"] == "READY_FOR_APPROVAL"
    assert "task_paused" in [e["action"] for e in run["events"]]
    assert sum(e["action"] == "costed" for e in run["events"]) == 1


def test_human_fact_confirmation_survives_resume_without_reextracting(client):
    run = resume(client, task(client, fixture="DEV-004"))
    assert run["state"] == "NEEDS_ENGINEERING_REVIEW"
    run = client.patch(
        f"/runs/{run['id']}/fields",
        headers=ENGINEER,
        json={
            "expected_version": run["version"],
            "values": {"drawing.material": "Al-6061-T6"},
            "reason": "Confirmed material from approved engineering supplement",
        },
    ).json()
    run = resume(client, run)
    assert run["state"] == "READY_FOR_APPROVAL"
    assert run["fields"]["drawing.material"]["original_value"] is None
    assert run["fields"]["drawing.material"]["human_confirmed"] is True
    assert sum(e["action"] == "extraction_started" for e in run["events"]) == 1


def test_drawing_only_review_has_no_fabricated_rfq_or_price(client):
    run = task(client, "review_drawing", None)
    assert run["orchestration"]["pause"]["missing"] == ["drawing"]
    run = upload(client, run, "drawing", ROOT / "fixtures/dev/drawings/PM-BRKT-010_B.pdf")
    run = resume(client, run)
    assert run["state"] == "REVIEW_COMPLETED"
    assert run["inputs"] is None and run["quote"] is None
    assert run["fields"]["rfq.quantity"]["value"] is None
    assert run["orchestration"]["result"]["limitations"]
    for endpoint, headers in [("cost", ENGINEER), ("export", APPROVER)]:
        response = client.post(
            f"/runs/{run['id']}/{endpoint}",
            headers=headers,
            json={"expected_version": run["version"]},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "INTENT_BOUNDARY"
    assert resume(client, run) == run


def test_drawing_review_rejects_unsupported_geometry(client):
    run = resume(client, task(client, "review_drawing"))
    run = client.patch(
        f"/runs/{run['id']}/fields",
        headers=ENGINEER,
        json={
            "expected_version": run["version"],
            "values": {"drawing.width_mm": "NaN"},
            "reason": "Test out-of-scope geometry",
        },
    ).json()
    run = resume(client, run)
    assert run["state"] == "NEEDS_ENGINEERING_REVIEW" and run["quote"] is None
    assert "TEMPLATE_GEOMETRY" in {i["code"] for i in run["issues"]}


def test_knowledge_missing_query_and_role_scoped_persisted_results(client, settings):
    run = task(client, "search_knowledge", None, headers=APPROVER)
    assert run["orchestration"]["pause"]["missing"] == ["query"]
    run = resume(client, run, headers=APPROVER, query={})
    assert run["state"] == "KNOWLEDGE_COMPLETED" and run["quote"] is None
    assert len(run["orchestration"]["result"]["results"]) == 9
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/runs/{run['id']}", headers=ENGINEER).status_code == 404
        assert run["id"] not in {r["id"] for r in restarted.get("/runs", headers=ENGINEER).json()}
        assert restarted.get(f"/runs/{run['id']}", headers=APPROVER).json() == run
    response = client.post(
        f"/tasks/{run['id']}/resume", headers=ENGINEER, json={"expected_version": run["version"]}
    )
    assert response.status_code == 404
    engineer_run = resume(client, task(client, "search_knowledge", None, query={}))
    assert len(engineer_run["orchestration"]["result"]["results"]) == 8


@pytest.mark.parametrize("intent", ["prepare_quote", "review_drawing"])
def test_approver_cannot_create_or_resume_engineering_intent(client, intent):
    assert client.post("/tasks", headers=APPROVER, json={"intent": intent}).status_code == 403
    run = task(client, intent, None)
    assert (
        client.post(
            f"/tasks/{run['id']}/resume",
            headers=APPROVER,
            json={"expected_version": run["version"]},
        ).status_code
        == 403
    )


def test_unresolved_task_cannot_be_adopted_by_other_role(client):
    run = task(client, None, None)
    assert (
        client.post(
            f"/tasks/{run['id']}/resume",
            headers=APPROVER,
            json={"expected_version": run["version"], "intent": "search_knowledge", "query": {}},
        ).status_code
        == 404
    )


def test_knowledge_intent_rejects_legacy_mutation_bypass(client, inputs):
    run = resume(client, task(client, "search_knowledge", None, query={}))
    base = f"/runs/{run['id']}"
    version = {"expected_version": run["version"]}
    for path, payload in [("analyze", version), ("cost", version)]:
        assert client.post(f"{base}/{path}", headers=ENGINEER, json=payload).status_code == 403
    assert (
        client.patch(
            f"{base}/inputs",
            headers=ENGINEER,
            json={**version, "inputs": inputs, "reason": "Cannot escalate a query"},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"{base}/fields",
            headers=ENGINEER,
            json={
                **version,
                "values": {"rfq.quantity": "500"},
                "reason": "Cannot escalate a query",
            },
        ).status_code
        == 403
    )
    assert client.get(base, headers=ENGINEER).json() == run


def test_legacy_task_cannot_use_orchestration_document_endpoint(client):
    run = client.post("/runs/from-fixture/DEV-001", headers=ENGINEER).json()
    response = client.post(
        f"/tasks/{run['id']}/documents", headers=ENGINEER, data={"expected_version": run["version"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LEGACY_TASK"


def test_interrupted_creation_never_exposes_private_request(client, monkeypatch):
    repo = client.app.state.workflow.repo
    original = repo.update

    def interrupted(run_id, version, actor, action, change, **kwargs):
        if action == "task_routed":
            raise RuntimeError("Simulated interruption after initial persistence")
        return original(run_id, version, actor, action, change, **kwargs)

    monkeypatch.setattr(repo, "update", interrupted)
    with pytest.raises(RuntimeError):
        client.post("/tasks", headers=APPROVER, json={"request": "Private approver request"})
    assert client.get("/runs", headers=ENGINEER).json() == []
    saved = client.get("/runs", headers=APPROVER).json()
    assert len(saved) == 1 and saved[0]["state"] == "CLARIFICATION_REQUIRED"
    run = resume(client, saved[0], headers=APPROVER, intent="search_knowledge", query={})
    assert run["state"] == "KNOWLEDGE_COMPLETED"


def test_intent_cannot_be_changed_and_stale_resume_is_rejected(client):
    run = task(client, "review_drawing")
    path = f"/tasks/{run['id']}/resume"
    assert (
        client.post(
            path,
            headers=ENGINEER,
            json={"expected_version": run["version"], "intent": "prepare_quote"},
        ).status_code
        == 409
    )
    resume(client, run)
    assert (
        client.post(path, headers=ENGINEER, json={"expected_version": run["version"]}).status_code
        == 409
    )
    assert client.post("/tasks", headers=ENGINEER, json={"intent": "approve"}).status_code == 422


@pytest.mark.parametrize("dependency", ["quantity", "drawing", "template", "rate"])
def test_m5_approved_draft_invalidates_for_each_dependency(client, settings, dependency):
    run = approve(client, resume(client, task(client)))
    if dependency == "quantity":
        run = client.patch(
            f"/runs/{run['id']}/fields",
            headers=ENGINEER,
            json={
                "expected_version": run["version"],
                "values": {"rfq.quantity": "500"},
                "reason": "Customer changed order quantity",
            },
        ).json()
    elif dependency == "drawing":
        run = upload(client, run, "drawing", ROOT / "fixtures/dev/drawings/PM-BRKT-010_A.pdf")
    else:
        reference = json.loads(settings.reference_path.read_text("utf-8"))
        reference[f"{dependency}_version"] += "-changed"
        settings.reference_path.write_text(json.dumps(reference), encoding="utf-8")
    response = client.post(
        f"/runs/{run['id']}/export", headers=APPROVER, json={"expected_version": run["version"]}
    )
    assert response.status_code in (409, 422)
    current = client.get(f"/runs/{run['id']}", headers=ENGINEER).json()
    assert current["quote"] is None and current["approval"] is None and current["export"] is None
    if dependency == "quantity":
        current = resume(client, current)
        assert current["quote"]["total_price"] == "12255.00"
        assert current["quote"]["draft_version"] == 2 and current["approval"] is None
    elif dependency in {"template", "rate"}:
        current = resume(client, current)
        assert current["state"] == "NEEDS_ENGINEERING_REVIEW"
        assert current["orchestration"]["pause"]["code"] == "NO_UNIQUE_BASIS"
