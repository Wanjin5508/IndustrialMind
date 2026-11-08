"""Independent M1 acceptance oracles for money, authorization and stale state."""

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from conftest import APPROVER, ENGINEER, approve, create_and_cost
from fastapi.testclient import TestClient

from rfq_workbench.errors import DomainError
from rfq_workbench.main import create_app
from rfq_workbench.repository import Repository


@pytest.mark.parametrize(
    "quantity,cost,price,total",
    [
        (100, "19.10", "25.47", "2547.00"),
        (500, "18.38", "24.51", "12255.00"),
        (3, "48.20", "64.27", "192.81"),
    ],
)
def test_price_matches_independent_arithmetic(client, inputs, quantity, cost, price, total):
    run = create_and_cost(client, inputs | {"quantity": quantity})
    quote = run["quote"]
    assert Decimal(quote["unit_cost"]) == Decimal(cost)
    assert quote["unit_price"] == price
    assert quote["total_price"] == total
    assert sum(Decimal(line["unit_cost"]) for line in quote["lines"]) == Decimal(cost)
    assert quote["pending"]


def test_revision_conflict_blocks_cost_and_approval(client, inputs):
    run = client.post(
        "/runs/confirmed", json=inputs | {"drawing_revision": "A"}, headers=ENGINEER
    ).json()
    assert run["state"] == "NEEDS_ENGINEERING_REVIEW"
    assert run["issues"][0]["code"] == "REVISION_CONFLICT"
    assert (
        client.post(
            f"/runs/{run['id']}/cost", json={"expected_version": 1}, headers=ENGINEER
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/runs/{run['id']}/export", json={"expected_version": 1}, headers=APPROVER
        ).status_code
        == 422
    )


def test_only_approver_can_approve_and_export(client, inputs):
    run = create_and_cost(client, inputs)
    payload = {
        "expected_version": run["version"],
        "technical_confirmed": True,
        "internal_only": True,
        "note": "Review complete",
    }
    assert (
        client.post(f"/runs/{run['id']}/approve", json=payload, headers=ENGINEER).status_code == 403
    )
    assert (
        client.post(
            f"/runs/{run['id']}/export", json={"expected_version": run["version"]}, headers=APPROVER
        ).status_code
        == 403
    )
    assert client.get("/runs").status_code == 401
    assert (
        client.post(
            f"/runs/{run['id']}/approve", json=payload | {"internal_only": False}, headers=APPROVER
        ).status_code
        == 422
    )


def test_quantity_change_invalidates_approved_draft_and_preserves_history(client, inputs):
    old = approve(client, create_and_cost(client, inputs))
    changed = client.patch(
        f"/runs/{old['id']}/inputs",
        json={
            "expected_version": old["version"],
            "inputs": inputs | {"quantity": 500},
            "reason": "Customer revised quantity",
        },
        headers=ENGINEER,
    ).json()
    assert changed["approval"] is None and changed["quote"] is None
    assert (
        client.post(
            f"/runs/{old['id']}/export",
            json={"expected_version": changed["version"]},
            headers=APPROVER,
        ).status_code
        == 422
    )
    new = client.post(
        f"/runs/{old['id']}/cost", json={"expected_version": changed["version"]}, headers=ENGINEER
    ).json()
    assert new["quote"]["total_price"] == "12255.00"
    assert new["quote"]["draft_version"] == 2
    assert new["quote"]["input_hash"] != old["quote"]["input_hash"]
    assert "approved" in [e["action"] for e in new["events"]]


def test_reference_change_invalidates_existing_approval(client, settings, inputs):
    run = approve(client, create_and_cost(client, inputs))
    ref = json.loads(settings.reference_path.read_text())
    ref["machine_hourly"] = "75.00"
    settings.reference_path.write_text(json.dumps(ref))
    response = client.post(
        f"/runs/{run['id']}/export", json={"expected_version": run["version"]}, headers=APPROVER
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STALE_DRAFT"
    refreshed = client.get(f"/runs/{run['id']}", headers=ENGINEER).json()
    assert refreshed["approval"] is None
    assert refreshed["events"][-1]["action"] == "dependency_invalidated"


def test_expired_rates_cannot_be_used(client, settings, inputs):
    ref = json.loads(settings.reference_path.read_text())
    ref["valid_until"] = "2020-01-01"
    settings.reference_path.write_text(json.dumps(ref))
    response = client.post("/runs/confirmed", json=inputs, headers=ENGINEER)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_RATE"


def test_export_is_persisted_and_repeat_does_not_duplicate_events(client, settings, inputs):
    run = approve(client, create_and_cost(client, inputs))
    exported = client.post(
        f"/runs/{run['id']}/export", json={"expected_version": run["version"]}, headers=APPROVER
    ).json()
    assert "INTERNAL BUDGET DRAFT" in exported["export"]["markdown"]
    with TestClient(create_app(settings)) as restarted:
        loaded = restarted.get(f"/runs/{run['id']}", headers=ENGINEER).json()
        assert loaded["export"] == exported["export"]
        repeated = restarted.post(
            f"/runs/{run['id']}/export",
            json={"expected_version": loaded["version"]},
            headers=APPROVER,
        ).json()
        assert repeated["version"] == loaded["version"]
        assert len(repeated["events"]) == len(loaded["events"])


def test_stale_and_concurrent_updates_do_not_overwrite(client, settings, inputs):
    run = create_and_cost(client, inputs)
    repo = Repository(settings.data_dir / "workbench.sqlite3")

    def update(value):
        try:

            def change(body):
                body["title"] = value
                return {"title": value}

            repo.update(run["id"], run["version"], "test", "test_change", change)
            return "ok"
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, ["first", "second"]))
    assert sorted(outcomes) == ["VERSION_CONFLICT", "ok"]
    assert repo.get(run["id"])["version"] == run["version"] + 1


def test_repository_scope_prevents_cross_project_read(client, settings, inputs):
    run = create_and_cost(client, inputs)
    repo = Repository(settings.data_dir / "workbench.sqlite3")
    with pytest.raises(DomainError) as error:
        repo.get(run["id"], project="OTHER-PROJECT")
    assert error.value.status == 404
