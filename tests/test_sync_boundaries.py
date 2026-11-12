"""Boundary regressions beyond the frozen happy/fault matrix."""

import json
import sys

import pytest
from conftest import ROOT

from rfq_workbench.canonical import canonical_json
from rfq_workbench.errors import DomainError
from rfq_workbench.integrations.faults import Failpoints, SimulatedCrash

sys.path.insert(0, str(ROOT / "scripts"))
from m9_scenarios import Environment  # noqa: E402


@pytest.fixture
def env(tmp_path):
    value = Environment(tmp_path)
    try:
        yield value
    finally:
        value.stack.close()


def test_expired_snapshot_never_sends(env):
    intent = env.enqueue()
    env.clock.advance(env.settings.snapshot_ttl_seconds + 1)
    result = env.sync.dispatch_once(intent["id"])
    assert result["state"] == "HELD" and result["last_error_code"] == "SNAPSHOT_STALE"
    assert env.systems.call_count("erp.put_draft") == 0


def test_process_paused_after_claim_cannot_send_with_expired_lease(env):
    intent = env.enqueue()
    env.sync.failpoints = Failpoints(
        lambda point: (
            env.clock.advance(env.settings.sync_lease_seconds + 1)
            if point == "after_claim"
            else None
        )
    )
    result = env.sync.dispatch_once(intent["id"])
    assert result["state"] == "UNKNOWN_OUTCOME"
    assert env.systems.call_count("erp.put_draft") == 0


def test_source_change_between_claim_and_send_is_held(env):
    intent = env.enqueue()
    env.sync.failpoints = Failpoints(
        lambda point: env.systems.plm.release_new_revision("C") if point == "after_claim" else None
    )
    result = env.sync.dispatch_once(intent["id"])
    assert result["state"] == "HELD" and result["attempt_log"][0]["outcome"] == "NOT_SENT"
    assert env.systems.call_count("erp.put_draft") == 0


def test_eventual_lookup_waits_without_resending(env, monkeypatch):
    intent = env.enqueue()
    env.systems.faults.add("erp.put_draft", "timeout_before_commit")
    assert env.sync.dispatch_once(intent["id"])["state"] == "UNKNOWN_OUTCOME"
    monkeypatch.setattr(env.sync.erp, "lookup_consistency", "eventual")
    assert env.sync.reconcile(intent["id"])["state"] == "UNKNOWN_OUTCOME"
    assert env.systems.call_count("erp.put_draft") == 1
    env.clock.advance(31)
    assert env.sync.reconcile(intent["id"])["state"] == "PREPARED"
    assert env.sync.dispatch_once(intent["id"])["state"] == "VERIFIED"
    assert env.systems.count_drafts() == 1


def test_failed_confirmation_reads_have_budget_and_do_not_resend(env):
    intent = env.enqueue()
    env.systems.faults.add("erp.put_draft", "timeout_after_commit")
    assert env.sync.dispatch_once(intent["id"])["state"] == "UNKNOWN_OUTCOME"
    env.systems.faults.add("erp.find_by_idempotency_key", "stale_read", times=2)
    assert env.sync.reconcile(intent["id"])["state"] == "UNKNOWN_OUTCOME"
    assert env.sync.reconcile(intent["id"])["state"] == "QUARANTINED"
    assert env.systems.count_drafts() == env.systems.call_count("erp.put_draft") == 1


def test_verified_drift_is_audited_without_reopening_intent(env):
    intent = env.enqueue()
    done = env.sync.dispatch_once(intent["id"])
    with env.systems.connect() as conn:
        row = conn.execute("SELECT * FROM drafts").fetchone()
        payload = json.loads(row["payload"])
        payload["total_price"] = "1.00"
        conn.execute("UPDATE drafts SET payload=?,version=version+1", (canonical_json(payload),))
    result = env.sync.reconcile(intent["id"])
    assert result["state"] == "VERIFIED" and result["reconciliation"]["status"] == "DIFF"
    assert result["receipt"] == done["receipt"]
    events = env.workflow.repo.get(env.run["id"])["events"]
    assert any(e["action"] == "target_drift_detected" for e in events)


def test_crash_after_claim_queries_before_first_send(env):
    intent = env.enqueue()
    env.crash("after_claim")
    with pytest.raises(SimulatedCrash):
        env.sync.dispatch_once(intent["id"])
    assert env.systems.call_count("erp.put_draft") == 0
    env.clock.advance(env.settings.sync_lease_seconds + 1)
    env.restart()
    assert env.sync.dispatch_once(intent["id"])["state"] == "PREPARED"
    assert env.sync.dispatch_once(intent["id"])["state"] == "VERIFIED"
    assert env.systems.count_drafts() == 1


def test_explicit_rebase_uses_confirmed_target_version_then_requires_approval(env):
    first = env.enqueue()
    done = env.sync.dispatch_once(first["id"])
    run = env.reapprove(env.mutate_quantity())
    second = env.enqueue(run)
    env.target_edit(done["external_id"])
    assert env.sync.dispatch_once(second["id"])["state"] == "DIVERGED"
    resolved = env.sync.resolve(second["id"], "rebase", "Reconcile target edit", "demo-approver")
    assert resolved["state"] == "SUPERSEDED"
    run = env.workflow.repo.get(env.run["id"])
    assert run["approval"] is None and run["quote"] is None
    with pytest.raises(DomainError):
        env.enqueue(run)
    run = env.reapprove(run)
    third = env.enqueue(run)
    assert third["expected_target_version"] == "2"
    assert env.sync.dispatch_once(third["id"])["state"] == "VERIFIED"
    assert env.systems.count_drafts() == 1
