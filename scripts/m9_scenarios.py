"""Executable simulated delivery scenarios shared by M9 regression and evaluation."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from shutil import copyfile
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient

from rfq_workbench.clock import FakeClock
from rfq_workbench.config import ROOT, Settings
from rfq_workbench.contracts import ConfirmedInputs
from rfq_workbench.costing import fingerprint
from rfq_workbench.errors import DomainError
from rfq_workbench.integrations.faults import Failpoints, SimulatedCrash
from rfq_workbench.integrations.mappers import map_quote_to_erp_draft_v1
from rfq_workbench.main import create_app
from rfq_workbench.sync import SyncService

ENGINEER = {"X-Demo-Token": "demo-engineer"}
APPROVER = {"X-Demo-Token": "demo-approver"}
INPUTS = {
    "part_number": "PM-BRKT-010",
    "required_revision": "B",
    "drawing_revision": "B",
    "quantity": 100,
    "material": "Al-6061-T6",
    "unit": "mm",
    "currency": "EUR",
    "plant": "DE-DEMO",
    "product_family": "mounting-bracket",
}


class Environment:
    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        reference, knowledge = folder / "manufacturing.json", folder / "knowledge.json"
        copyfile(ROOT / "fixtures/reference/manufacturing.json", reference)
        copyfile(ROOT / "fixtures/reference/knowledge.json", knowledge)
        self.settings = Settings(
            data_dir=folder / "data",
            reference_path=reference,
            knowledge_path=knowledge,
            sync_max_attempts=2,
        )
        self.clock = FakeClock()
        self.stack = ExitStack()
        self.restart()
        self.run = self.approved()

    def restart(self, failpoints=None):
        self.app = create_app(self.settings, clock=self.clock, failpoints=failpoints)
        self.client = self.stack.enter_context(TestClient(self.app))
        self.workflow, self.sync, self.systems = (
            self.app.state.workflow,
            self.app.state.sync,
            self.app.state.systems,
        )
        self.sync.jitter = lambda: 0

    @staticmethod
    def success(response):
        assert response.is_success, (response.status_code, response.text)
        return response.json()

    def approved(self):
        run = self.success(self.client.post("/runs/confirmed", headers=ENGINEER, json=INPUTS))
        return self.reapprove(run)

    def reapprove(self, run):
        run = self.workflow.cost(run["id"], run["version"], "demo-engineer")
        return self.success(
            self.client.post(
                f"/runs/{run['id']}/approve",
                headers=APPROVER,
                json={
                    "expected_version": run["version"],
                    "technical_confirmed": True,
                    "internal_only": True,
                    "note": "Independent synthetic M9 review",
                },
            )
        )

    def enqueue(self, run=None):
        run = run or self.workflow.repo.get(self.run["id"])
        return self.sync.request_sync(
            run["id"], run["version"], "demo-approver", "Synthetic ERP draft"
        )

    def mutate_quantity(self, quantity=500):
        run = self.workflow.repo.get(self.run["id"])
        return self.workflow.change_inputs(
            run["id"],
            run["version"],
            ConfirmedInputs.model_validate(INPUTS | {"quantity": quantity}),
            "Customer changed quantity",
            "demo-engineer",
        )

    def target_edit(self, identifier):
        with self.systems.connect() as conn:
            conn.execute("UPDATE drafts SET version=version+1 WHERE external_id=?", (identifier,))

    def crash(self, name):
        def hit(point):
            if point == name:
                raise SimulatedCrash(point)

        self.sync.failpoints = Failpoints(hit)

    def result(self, intent, **extra):
        final = self.sync.store.get(intent["id"])
        rows = self.systems.erp.find_by_business_key(final.business_key)
        return {
            "state": final.state,
            "draft_count": len(rows),
            "put_calls": self.systems.call_count("erp.put_draft"),
            "attempts": final.attempts,
            **extra,
        }


def scenario(identifier: str, folder: Path) -> dict:
    env = Environment(folder)
    try:
        return execute(identifier, env)
    finally:
        env.stack.close()


def execute(identifier: str, env: Environment) -> dict:
    sync, systems = env.sync, env.systems
    if identifier == "M9-13":
        errors = []
        for field, value in (("unit", "inch"), ("plant", "PL-DEMO")):
            try:
                map_quote_to_erp_draft_v1(
                    env.run["quote"],
                    env.run["inputs"] | {field: value},
                    env.run["id"],
                    env.run["approval"],
                )
            except DomainError as exc:
                errors.append(exc.code)
        quote = json.loads(json.dumps(env.run["quote"]))
        quote["lines"][0]["batch_cost"] = "800.02"
        try:
            map_quote_to_erp_draft_v1(
                quote, env.run["inputs"], env.run["id"], {"quote_hash": fingerprint(quote)}
            )
        except DomainError as exc:
            errors.append(exc.code)
        assert errors == ["MAPPING_UNSUPPORTED", "MAPPING_UNSUPPORTED", "MAPPING_PRECISION"]
        return {"state": "BLOCKED", "draft_count": systems.count_drafts(), "errors": errors}
    if identifier == "M9-14":
        systems.faults.add("plm.read_item_revision", "schema_drift")
        body = env.mutate_quantity(100)
        try:
            env.workflow.cost(body["id"], body["version"], "demo-engineer")
            raise AssertionError("missing field was accepted")
        except DomainError as exc:
            assert exc.code == "SNAPSHOT_SCHEMA_DRIFT"
        raw = systems.erp.read_cost_reference("Al-6061-T6", "DE-DEMO")
        systems.replace_resource(raw.ref, raw.payload | {"new_optional_field": "keep trace"})
        env.run = env.reapprove(body)
        snapshots = [env.workflow.snapshots.get(id) for id in env.run["snapshot_refs"]["ids"]]
        assert any("new_optional_field" in s.unknown_fields for s in snapshots)
    if identifier == "M9-16":
        body = {
            "expected_version": env.run["version"],
            "target": "ERP",
            "note": "Idempotent request",
        }
        headers = APPROVER | {"Idempotency-Key": "m9-same-request-key"}
        url = f"/runs/{env.run['id']}/sync"
        first = env.client.post(url, headers=headers, json=body)
        assert first.is_success
        again = env.client.post(url, headers=headers, json=body)
        assert first.status_code == again.status_code and first.json() == again.json()
        changed = env.client.post(url, headers=headers, json=body | {"note": "Changed"})
        assert (
            changed.status_code == 422
            and changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        )
        intent = first.json()
        sync.dispatch_once(intent["id"])
        return env.result(intent)

    intent = env.enqueue()
    key = intent["id"]
    if identifier in {"M9-02", "M9-03", "M9-04", "M9-12"}:
        fault = {
            "M9-02": "timeout_before_commit",
            "M9-03": "timeout_after_commit",
            "M9-04": "duplicate_delivery",
            "M9-12": "partial_write",
        }[identifier]
        systems.faults.add("erp.put_draft", fault)
        first = sync.dispatch_once(key)
        if identifier in {"M9-02", "M9-03"}:
            assert first["state"] == "UNKNOWN_OUTCOME"
            looked_up = sync.reconcile(key)
            assert looked_up["state"] == ("PREPARED" if identifier == "M9-02" else "VERIFIED")
            sync.dispatch_once(key)
            assert systems.call_count("erp.put_draft") == (2 if identifier == "M9-02" else 1)
        if identifier == "M9-12":
            assert first["reconciliation"]["status"] == "DIFF"
    elif identifier == "M9-05":
        env.crash("after_send_before_record")
        try:
            sync.dispatch_once(key)
            raise AssertionError("crash not injected")
        except SimulatedCrash:
            pass
        env.clock.advance(env.settings.sync_lease_seconds + 1)
        env.restart()
        env.sync.dispatch_once(key)
        assert env.systems.call_count("erp.put_draft") == 1
    elif identifier in {"M9-06", "M9-07"}:
        entered, release = Event(), Event()
        original = sync.erp.put_draft

        def slow(*args, **kwargs):
            raw = original(*args, **kwargs)
            entered.set()
            assert release.wait(10)
            return raw

        sync.erp.put_draft = slow
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(sync.dispatch_once, key)
            try:
                assert entered.wait(5)
                other = SyncService(env.workflow, jitter=lambda: 0)
                if identifier == "M9-07":
                    env.clock.advance(env.settings.sync_lease_seconds + 1)
                    assert other.reconcile(key)["state"] == "VERIFIED"
                else:
                    assert other.dispatch_once(key)["state"] == "DISPATCHING"
            finally:
                release.set()
            future.result(timeout=5)
        assert systems.call_count("erp.put_draft") == 1
        if identifier == "M9-07":
            assert sync.view(sync.store.get(key))["attempt_log"][0]["outcome"] == "LATE"
    elif identifier in {"M9-08", "M9-15"}:
        original = sync.dispatch_once(key)
        run = env.reapprove(env.mutate_quantity())
        updated = env.enqueue(run)
        assert updated["operation"] == "UPDATE_DRAFT"
        if identifier == "M9-08":
            env.target_edit(original["external_id"])
        second = sync.dispatch_once(updated["id"])
        assert second["external_id"] == original["external_id"]
        if identifier == "M9-15":
            assert second["receipt"]["target_version"] == "2"
            assert (
                systems.erp.read_draft(second["external_id"]).payload["total_price"] == "12255.00"
            )
        else:
            assert second["state"] == "DIVERGED"
            assert systems.erp.read_draft(second["external_id"]).payload["total_price"] == "2547.00"
        return env.result(updated)
    elif identifier == "M9-09":
        states = []
        for fault in ("http_429", "http_503"):
            # Separate task per transport category, same persistent target store.
            run = env.approved()
            request = env.enqueue(run)
            systems.faults.add("erp.put_draft", fault, times=3)
            for _ in range(6):
                current = sync.dispatch_once(request["id"])
                env.clock.advance(10)
                if current["state"] == "QUARANTINED":
                    break
            assert current["state"] == "QUARANTINED"
            states.append(current["state"])
            with systems.connect() as conn:
                conn.execute("UPDATE faults SET remaining=0")
        return {"state": "QUARANTINED", "draft_count": systems.count_drafts(), "variants": states}
    elif identifier == "M9-10":
        systems.plm.release_new_revision("C")
        assert sync.dispatch_once(key)["state"] == "HELD"
    elif identifier == "M9-11":
        env.mutate_quantity()
        assert sync.dispatch_once(key)["state"] == "HELD"
        assert sync.dispatch_once(key)["state"] == "SUPERSEDED"
    elif identifier == "M9-17":
        env.crash("after_record_before_verify")
        try:
            sync.dispatch_once(key)
            raise AssertionError("crash not injected")
        except SimulatedCrash:
            pass
        with systems.connect() as conn:
            row = conn.execute("SELECT * FROM drafts").fetchone()
            conn.execute(
                "INSERT INTO drafts VALUES(?,?,?,?,?)",
                (uuid4().hex, row["business_key"], row["version"], row["as_of"], row["payload"]),
            )
        final = sync.reconcile(key)
        assert final["state"] == "DIVERGED" and final["reconciliation"]["status"] == "DUPLICATE"
    else:
        sync.dispatch_once(key)
    result = env.result(intent)
    if identifier in {"M9-01", "M9-04", "M9-06", "M9-07", "M9-14"}:
        assert result["state"] == "VERIFIED" and result["draft_count"] == 1
    return result
