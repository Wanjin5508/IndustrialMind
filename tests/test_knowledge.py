"""M4: deny-before-search, reference-only history, and fresh pricing dependencies."""

import json

import pytest
from conftest import APPROVER, ENGINEER, approve, create_and_cost
from test_files import analyze, cost, fixture_run


def change_record(settings, record_id, **changes):
    records = json.loads(settings.knowledge_path.read_text("utf-8"))
    next(r for r in records if r["id"] == record_id).update(changes)
    settings.knowledge_path.write_text(json.dumps(records), encoding="utf-8")


def test_search_filters_before_returning_metadata(client):
    assert client.post("/knowledge/search", json={}).status_code == 401
    response = client.post("/knowledge/search", json={}, headers=ENGINEER)
    rows = response.json()["results"]
    ids = {r["id"] for r in rows}
    assert {"KB-CATALOG", "KB-TEMPLATE", "KB-RATE", "KB-HISTORY"} <= ids
    assert not {"KB-PRIVATE", "KB-OTHER-PROJECT", "KB-OTHER-PLANT", "KB-OTHER-FAMILY"} & ids
    assert all(r["origin_type"] == "synthetic" for r in rows)
    assert {r["id"] for r in rows if r["eligible_for_cost"]} == {
        "KB-CATALOG",
        "KB-TEMPLATE",
        "KB-RATE",
    }
    for record_id in ["KB-PRIVATE", "KB-OTHER-PROJECT", "KB-OTHER-PLANT", "does-not-exist"]:
        assert client.get(f"/knowledge/{record_id}", headers=ENGINEER).status_code == 404
    assert client.get("/knowledge/KB-PRIVATE", headers=APPROVER).status_code == 200
    assert (
        client.post(
            "/knowledge/search", json={"project_id": "OTHER-PROJECT"}, headers=ENGINEER
        ).status_code
        == 422
    )
    assert (
        client.post("/knowledge/search", json={"query": "Other project"}, headers=ENGINEER).json()[
            "results"
        ]
        == []
    )


def test_reference_only_has_explicit_reasons_and_never_sets_price(client, inputs):
    rows = {
        r["id"]: r
        for r in client.post("/knowledge/search", json={}, headers=ENGINEER).json()["results"]
    }
    for identity, reason in [
        ("KB-EXPIRED", "OUTSIDE_VALIDITY"),
        ("KB-DRAFT", "UNAPPROVED"),
        ("KB-STEEL", "MISMATCH_MATERIAL"),
        ("KB-REV-A", "MISMATCH_REVISION"),
        ("KB-HISTORY", "REFERENCE_ONLY"),
    ]:
        assert reason in rows[identity]["reasons"] and not rows[identity]["eligible_for_cost"]
    quote = create_and_cost(client, inputs)["quote"]
    assert quote["unit_price"] == "25.47"
    assert {r["source_id"] for r in quote["knowledge_basis"]} == {
        "KB-CATALOG",
        "KB-TEMPLATE",
        "KB-RATE",
    }
    assert len(quote["routing"]) == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"approved": False},
        {"valid_until": "2020-01-01"},
        {"plant": "PL-DEMO"},
        {"project_id": "OTHER-PROJECT"},
        {"product_family": "other"},
        {"roles": ["demo-approver"]},
        {"material": "Steel-S235"},
        {"revisions": ["A"]},
        {"version": "wrong-version"},
    ],
)
def test_inapplicable_basis_cannot_enter_price(client, settings, changes):
    run = analyze(client, fixture_run(client))
    change_record(settings, "KB-TEMPLATE", **changes)
    response = cost(client, run)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_UNIQUE_BASIS"
    assert client.get(f"/runs/{run['id']}", headers=ENGINEER).json()["quote"] is None


def test_revocation_invalidates_approval(client, settings, inputs):
    run = approve(client, create_and_cost(client, inputs))
    change_record(settings, "KB-RATE", approved=False)
    response = client.post(
        f"/runs/{run['id']}/export", headers=APPROVER, json={"expected_version": run["version"]}
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "STALE_DRAFT"
    current = client.get(f"/runs/{run['id']}", headers=ENGINEER).json()
    assert current["approval"] is None


def test_unrelated_history_edit_does_not_change_quote_dependency(client, settings, inputs):
    run = approve(client, create_and_cost(client, inputs))
    change_record(settings, "KB-HISTORY", content="Historical price EUR 1.00; reference only")
    response = client.post(
        f"/runs/{run['id']}/export", headers=APPROVER, json={"expected_version": run["version"]}
    )
    assert response.status_code == 200
    assert response.json()["quote"]["total_price"] == "2547.00"


def test_duplicate_eligible_rate_is_not_arbitrarily_selected(client, settings, inputs):
    rows = json.loads(settings.knowledge_path.read_text("utf-8"))
    rows.append(next(r for r in rows if r["id"] == "KB-RATE") | {"id": "KB-RATE-SECOND"})
    settings.knowledge_path.write_text(json.dumps(rows), encoding="utf-8")
    run = client.post("/runs/confirmed", headers=ENGINEER, json=inputs).json()
    assert cost(client, run).json()["error"]["code"] == "NO_UNIQUE_BASIS"


def test_current_template_geometry_rechecked_at_cost(client, settings):
    run = analyze(client, fixture_run(client))
    reference = json.loads(settings.reference_path.read_text())
    reference["applicability"]["dimensions"]["width_mm"] = "100"
    settings.reference_path.write_text(json.dumps(reference))
    assert cost(client, run).status_code == 422


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"part_number": "UNKNOWN"}, "NO_TEMPLATE"),
        ({"drawing_revision": "A", "required_revision": "A"}, "UNAPPROVED_REVISION"),
    ],
)
def test_confirmed_inputs_cannot_bypass_supported_part_and_revision(client, inputs, changes, code):
    run = client.post("/runs/confirmed", headers=ENGINEER, json=inputs | changes).json()
    assert code in {i["code"] for i in run["issues"]}
    assert cost(client, run).status_code == 422
