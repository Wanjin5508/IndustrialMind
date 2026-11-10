"""Source failures and drift block approval without I/O in task transactions."""

import pytest
from conftest import APPROVER, ENGINEER, create_and_cost

from rfq_workbench.clock import FakeClock
from rfq_workbench.contracts import ApproveRequest
from rfq_workbench.errors import DomainError


def test_snapshot_probes_fail_closed_without_changing_quote(client, inputs):
    run = create_and_cost(client, inputs)
    workflow = client.app.state.workflow
    assert len(run["snapshot_refs"]["ids"]) == 3
    original_hash = run["quote"]["reference_hash"]
    workflow.systems.plm.release_new_revision("C")
    response = client.post(
        f"/runs/{run['id']}/approve",
        headers=APPROVER,
        json={
            "expected_version": run["version"],
            "technical_confirmed": True,
            "internal_only": True,
            "note": "Review",
        },
    )
    assert response.json()["error"]["code"] == "SNAPSHOT_STALE"
    assert workflow.repo.get(run["id"])["quote"]["reference_hash"] == original_hash


def test_source_drift_missing_key_and_additive_fields(client, inputs):
    workflow = client.app.state.workflow
    run = client.post("/runs/confirmed", headers=ENGINEER, json=inputs).json()
    raw = workflow.systems.erp.read_cost_reference(inputs["material"], inputs["plant"])
    workflow.systems.faults.add("erp.read_cost_reference", "schema_drift")
    with pytest.raises(DomainError) as error:
        workflow.cost(run["id"], run["version"], "demo-engineer")
    assert error.value.code == "SNAPSHOT_SCHEMA_DRIFT"
    workflow.systems.replace_resource(raw.ref, raw.payload | {"supplier_note": "new optional data"})
    costed = workflow.cost(run["id"], run["version"], "demo-engineer")
    snapshots = [workflow.snapshots.get(id) for id in costed["snapshot_refs"]["ids"]]
    assert "supplier_note" in next(s for s in snapshots if s.ref.system == "ERP").unknown_fields


def test_no_port_calls_inside_update_and_expired_snapshot_blocks(client, inputs, monkeypatch):
    workflow = client.app.state.workflow
    active = False
    original = workflow.repo.update_tx

    def update(*args, **kwargs):
        nonlocal active
        active = True
        try:
            return original(*args, **kwargs)
        finally:
            active = False

    monkeypatch.setattr(workflow.repo, "update_tx", update)
    for port, method in [
        (workflow.systems.plm, "read_item_revision"),
        (workflow.systems.erp, "read_cost_reference"),
        (workflow.systems.mes, "read_feedback"),
    ]:
        read = getattr(port, method)

        def checked(*args, _read=read):
            assert not active, "external I/O entered update callback"
            return _read(*args)

        monkeypatch.setattr(port, method, checked)
    run = create_and_cost(client, inputs)
    clock = FakeClock(workflow.clock.now())
    workflow.clock = workflow.snapshots.clock = clock
    clock.advance(workflow.settings.snapshot_ttl_seconds + 1)
    with pytest.raises(DomainError) as error:
        workflow.approve(
            run["id"],
            ApproveRequest(
                expected_version=run["version"],
                technical_confirmed=True,
                internal_only=True,
                note="Review",
            ),
            "demo-approver",
        )
    assert error.value.code == "SNAPSHOT_STALE"
