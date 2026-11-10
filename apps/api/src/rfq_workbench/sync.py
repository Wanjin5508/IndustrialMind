"""ERP outbox with fenced leases, unknown-outcome lookup and read-after-write checks."""

import random
import sqlite3
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from rfq_workbench.canonical import canonical_hash
from rfq_workbench.costing import fingerprint
from rfq_workbench.errors import DomainError
from rfq_workbench.integrations.contracts import (
    FieldDiff,
    OutboundIntent,
    Receipt,
    ReconciliationResult,
)
from rfq_workbench.integrations.faults import Failpoints
from rfq_workbench.integrations.mappers import MAPPING_VERSION, map_quote_to_erp_draft_v1
from rfq_workbench.integrations.ports import HttpError, TransportError
from rfq_workbench.sync_store import SyncStore

ACTIVE_SEND = {"PREPARED", "RETRY_WAIT", "HELD"}
MONEY_FIELDS = {"unit_cost", "batch_cost", "unit_price", "total_price"}


def compare_payload(expected: object, actual: object, field: str = "") -> list[FieldDiff]:
    """Compare every field and line. Missing fields and extra fields are differences."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs = []
        for key in sorted(set(expected) | set(actual)):
            location = f"{field}.{key}" if field else key
            if key not in expected or key not in actual:
                diffs.append(
                    FieldDiff(
                        field=location, expected=str(expected.get(key)), actual=str(actual.get(key))
                    )
                )
            else:
                diffs.extend(compare_payload(expected[key], actual[key], location))
        return diffs
    if isinstance(expected, list) and isinstance(actual, list):
        diffs = []
        if len(expected) != len(actual):
            diffs.append(
                FieldDiff(
                    field=field + ".length", expected=str(len(expected)), actual=str(len(actual))
                )
            )
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            diffs.extend(compare_payload(left, right, f"{field}[{index}]"))
        return diffs
    equal = type(expected) is type(actual) and expected == actual
    if field.split(".")[-1] in MONEY_FIELDS and isinstance(actual, str):
        try:
            equal = Decimal(str(expected)) == Decimal(actual)
        except InvalidOperation:
            equal = False
    return [] if equal else [FieldDiff(field=field, expected=str(expected), actual=str(actual))]


class SyncService:
    def __init__(self, workflow, failpoints=None, jitter=None):
        self.workflow = workflow
        self.repo, self.settings, self.clock = workflow.repo, workflow.settings, workflow.clock
        self.erp = workflow.systems.erp
        self.store = SyncStore(self.repo, self.clock)
        self.failpoints = failpoints or Failpoints()
        self.jitter = jitter or random.random

    @staticmethod
    def approved(body: dict, expected_hash: str | None = None) -> bool:
        quote, approval = body.get("quote"), body.get("approval") or {}
        return bool(
            body["state"] in {"APPROVED_DRAFT", "EXPORTED_DRAFT"}
            and quote
            and approval.get("quote_hash") == fingerprint(quote)
            and (expected_hash is None or approval["quote_hash"] == expected_hash)
        )

    def request_sync(self, run_id: str, version: int, actor: str, note: str) -> dict:
        if actor != "demo-approver":
            raise DomainError("FORBIDDEN", "同步需要审批人权限。", 403)
        body = self.repo.get(run_id)
        if not self.approved(body):
            raise DomainError("SYNC_NOT_APPROVED", "请先批准当前草案。", 409)
        if not self.workflow.references_match(body, actor, probe=True):
            raise DomainError("STALE_DRAFT", "报价依据已变化，请重新计价和审批。", 409)
        previous = self.store.list(run_id)
        for intent in previous:
            if (
                intent.state == "VERIFIED"
                and intent.approved_quote_hash == body["approval"]["quote_hash"]
            ):
                return self.view(intent)
        verified = [i for i in previous if i.state == "VERIFIED"]
        latest = verified[-1] if verified else None
        # Preserve target identity even when a later attempt required human resolution.
        known = [i for i in previous if i.external_id and i.receipt]
        anchor = known[-1] if known else latest
        operation = "UPDATE_DRAFT" if anchor else "CREATE_DRAFT"
        payload = map_quote_to_erp_draft_v1(
            body["quote"], body["inputs"], run_id, body["approval"], operation
        ).model_dump()
        intent = OutboundIntent(
            id=uuid4().hex,
            run_id=run_id,
            operation=operation,
            business_key=f"{run_id}:ERP",
            idempotency_key=payload["x_idempotency_key"],
            payload=payload,
            payload_sha256=canonical_hash(payload),
            approved_quote_hash=body["approval"]["quote_hash"],
            base_snapshot_ids=body["snapshot_refs"]["ids"],
            mapping_version=MAPPING_VERSION,
            external_id=anchor.external_id if anchor else None,
            expected_target_version=(
                anchor.expected_target_version
                if anchor and anchor.state == "SUPERSEDED"
                else anchor.receipt.target_version
                if anchor
                else None
            ),
            requested_by=actor,
            max_attempts=self.settings.sync_max_attempts,
            created_at=self.clock.now(),
            updated_at=self.clock.now(),
        )

        def enqueue(current, conn):
            if not self.approved(current, intent.approved_quote_hash):
                raise DomainError("SYNC_NOT_APPROVED", "草案已变化，请重新批准。", 409)
            if current.get("snapshot_refs") != body.get(
                "snapshot_refs"
            ) or not self.workflow.references_match(current, actor):
                raise DomainError("SYNC_PRECONDITION_FAILED", "来源依据已变化。", 409)
            self.store.save(conn, intent, insert=True)
            return {"intent_id": intent.id, "note": note, "payload_sha256": intent.payload_sha256}

        try:
            self.repo.update_tx(run_id, version, actor, "sync_requested", enqueue)
        except sqlite3.IntegrityError as exc:
            raise DomainError(
                "SYNC_ALREADY_ACTIVE", "当前任务已有同步意图，请先处理。", 409
            ) from exc
        return self.view(intent)

    def view(self, intent) -> dict:
        row = next(row for row in self.store.summary(intent.run_id) if row["id"] == intent.id)
        return row | intent.model_dump(mode="json")

    def transition(self, intent, state: str, actor: str, code: str | None = None) -> OutboundIntent:
        with self.repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.store.get(intent.id, conn)
            if current.updated_at != intent.updated_at or current.state != intent.state:
                return current
            current.state, current.updated_at = state, self.clock.now()
            current.last_error_code = code
            self.store.save(conn, current)
            self.store.event(conn, current, "sync_state_changed", actor, code=code)
        return self.store.get(intent.id)

    def preflight(self, intent) -> str | None:
        body = self.repo.get(intent.run_id)
        if not self.approved(body, intent.approved_quote_hash):
            return "SYNC_NOT_APPROVED"
        if not self.workflow.references_match(body, intent.requested_by, probe=True):
            return "STALE_DRAFT"
        self.workflow.snapshots.probe(body, intent.base_snapshot_ids)
        if canonical_hash(intent.payload) != intent.payload_sha256:
            return "SYNC_PRECONDITION_FAILED"
        if intent.operation == "UPDATE_DRAFT":
            target = self.erp.read_draft(intent.external_id)
            if target.version != intent.expected_target_version:
                return "TARGET_DIVERGED"
        return None

    def dispatch_once(self, identifier: str, triggered_by: str = "system-dispatcher") -> dict:
        intent = self.store.get(identifier)
        if intent.state == "DISPATCHING":
            self.recover(identifier, triggered_by)
            intent = self.store.get(identifier)
        if intent.state == "UNKNOWN_OUTCOME":
            return self.reconcile_unknown(intent, triggered_by)
        if intent.state == "ACKED":
            return self.verify(intent, triggered_by)
        if intent.state not in ACTIVE_SEND:
            return self.view(intent)
        if intent.next_attempt_at and self.clock.now() < intent.next_attempt_at:
            return self.view(intent)
        try:
            code = self.preflight(intent)
        except Exception as exc:
            code = exc.code if isinstance(exc, DomainError) else "SNAPSHOT_UNAVAILABLE"
        if code:
            state = "DIVERGED" if code == "TARGET_DIVERGED" else "HELD"
            if intent.state == "HELD" and code == "SYNC_NOT_APPROVED":
                state = "SUPERSEDED"
            return self.view(self.transition(intent, state, triggered_by, code))
        with self.repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.store.get(identifier, conn)
            if current.state != intent.state or current.updated_at != intent.updated_at:
                return self.view(current)
            body = self.repo._get(conn, intent.run_id, "PM-DEMO")
            if not self.approved(body, intent.approved_quote_hash):
                current.state = "HELD"
                current.last_error_code = "SYNC_NOT_APPROVED"
                current.updated_at = self.clock.now()
                self.store.save(conn, current)
                self.store.event(conn, current, "sync_held", triggered_by)
                return self.view(current)
            if (
                not self.workflow.references_match(body, intent.requested_by)
                or body.get("snapshot_refs", {}).get("ids") != intent.base_snapshot_ids
            ):
                current.state, current.last_error_code = "HELD", "SYNC_PRECONDITION_FAILED"
                current.updated_at = self.clock.now()
                self.store.save(conn, current)
                self.store.event(conn, current, "sync_held", triggered_by)
                return self.view(current)
            if current.attempts >= current.max_attempts:
                current.state = "QUARANTINED"
                current.last_error_code = "RETRY_LIMIT"
                self.store.save(conn, current)
                self.store.event(conn, current, "sync_quarantined", triggered_by)
                return self.view(current)
            current.state = "DISPATCHING"
            current.attempts += 1
            current.lease_token = uuid4().hex
            current.lease_until = self.clock.now() + timedelta(
                seconds=self.settings.sync_lease_seconds
            )
            current.updated_at = self.clock.now()
            self.store.save(conn, current)
            conn.execute(
                "INSERT INTO outbox_attempts(intent_id,attempt,started_at,request_sha256) VALUES(?,?,?,?)",
                (
                    identifier,
                    current.attempts,
                    self.clock.now().isoformat(),
                    current.payload_sha256,
                ),
            )
            self.store.event(conn, current, "sync_claimed", triggered_by)
            intent = current
        self.failpoints.hit("after_claim")
        started = self.clock.now()
        try:
            raw = self.erp.put_draft(
                intent.business_key,
                intent.payload,
                intent.idempotency_key,
                intent.expected_target_version,
            )
        except Exception as exc:
            self.complete(intent, None, exc, started, triggered_by)
            return self.view(self.store.get(identifier))
        self.failpoints.hit("after_send_before_record")
        completed = self.complete(intent, raw, None, started, triggered_by)
        self.failpoints.hit("after_record_before_verify")
        return (
            self.verify(completed, triggered_by)
            if completed.state == "ACKED"
            else self.view(completed)
        )

    def complete(self, intent, raw, error, started, actor) -> OutboundIntent:
        with self.repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.store.get(intent.id, conn)
            if current.state != "DISPATCHING" or current.lease_token != intent.lease_token:
                conn.execute(
                    "UPDATE outbox_attempts SET outcome='LATE',error_code='LEASE_LOST',finished_at=? "
                    "WHERE intent_id=? AND attempt=?",
                    (self.clock.now().isoformat(), intent.id, intent.attempts),
                )
                self.store.event(conn, current, "sync_late_result", actor, code="LEASE_LOST")
                return current
            status = error.status if isinstance(error, HttpError) else None
            if raw:
                current.receipt = self.receipt(raw)
                current.external_id = raw.ref.external_id
                current.state = "ACKED"
                current.last_error_class = current.last_error_code = None
            else:
                current.state, current.last_error_class, current.last_error_code = self.classify(
                    error
                )
                if current.state == "RETRY_WAIT":
                    if current.attempts >= current.max_attempts:
                        current.state = "QUARANTINED"
                    else:
                        delay = min(300, 2**current.attempts) + self.jitter()
                        retry_after = error.retry_after if isinstance(error, HttpError) else 0
                        current.next_attempt_at = self.clock.now() + timedelta(
                            seconds=max(delay, retry_after)
                        )
            current.lease_until = current.lease_token = None
            current.updated_at = self.clock.now()
            self.store.save(conn, current)
            conn.execute(
                "UPDATE outbox_attempts SET finished_at=?,outcome=?,error_class=?,error_code=?,"
                "http_status=?,response_sha256=?,elapsed_ms=? WHERE intent_id=? AND attempt=?",
                (
                    self.clock.now().isoformat(),
                    current.state,
                    current.last_error_class,
                    current.last_error_code,
                    status,
                    current.receipt.response_sha256 if raw else None,
                    round((self.clock.now() - started).total_seconds() * 1000),
                    intent.id,
                    intent.attempts,
                ),
            )
            self.store.event(conn, current, "sync_attempt_finished", actor)
        return self.store.get(intent.id)

    def classify(self, error) -> tuple[str, str, str]:
        if isinstance(error, TransportError):
            return (
                "RETRY_WAIT" if error.kind == "connect" else "UNKNOWN_OUTCOME",
                "retryable" if error.kind == "connect" else "unknown",
                "TRANSPORT_" + error.kind.upper(),
            )
        if isinstance(error, HttpError):
            status = error.status
            if status == 429 or status in self.erp.not_processed_statuses:
                return "RETRY_WAIT", "retryable", f"HTTP_{status}"
            if status in {409, 412}:
                return "DIVERGED", "permanent", f"HTTP_{status}"
            if status in {400, 422}:
                return "REJECTED", "permanent", f"HTTP_{status}"
            if status in {401, 403}:
                return "QUARANTINED", "permanent", f"HTTP_{status}"
            return "UNKNOWN_OUTCOME", "unknown", f"HTTP_{status}"
        return "UNKNOWN_OUTCOME", "unknown", "UNKNOWN_TRANSPORT"

    def receipt(self, raw) -> Receipt:
        return Receipt(
            external_id=raw.ref.external_id,
            target_version=raw.version,
            acked_at=self.clock.now(),
            response_sha256=canonical_hash(raw.payload),
        )

    def recover(self, identifier: str, actor: str = "system-dispatcher") -> dict:
        intent = self.store.get(identifier)
        if intent.state != "DISPATCHING" or intent.lease_until > self.clock.now():
            return self.view(intent)
        with self.repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.store.get(identifier, conn)
            if current.state == "DISPATCHING" and current.lease_token == intent.lease_token:
                current.state, current.updated_at = "UNKNOWN_OUTCOME", self.clock.now()
                current.lease_token = current.lease_until = None
                current.last_error_class, current.last_error_code = "unknown", "LEASE_EXPIRED"
                self.store.save(conn, current)
                conn.execute(
                    "UPDATE outbox_attempts SET outcome='UNKNOWN_OUTCOME',finished_at=?,"
                    "error_code='LEASE_EXPIRED' WHERE intent_id=? AND attempt=?",
                    (self.clock.now().isoformat(), identifier, current.attempts),
                )
                self.store.event(conn, current, "sync_recovered", actor)
        return self.view(self.store.get(identifier))

    def verify(self, intent, actor: str = "system-dispatcher") -> dict:
        if intent.state not in {"ACKED", "VERIFIED"}:
            return self.view(intent)
        try:
            records = self.erp.find_by_business_key(intent.business_key)
            if len(records) != 1:
                result = ReconciliationResult(
                    status="DUPLICATE" if len(records) > 1 else "MISSING",
                    checked_at=self.clock.now(),
                )
            else:
                target = self.erp.read_draft(intent.external_id)
                diffs = compare_payload(intent.payload, target.payload)
                result = ReconciliationResult(
                    status="DIFF" if diffs else "MATCH",
                    diffs=diffs,
                    target_version=target.version,
                    checked_at=self.clock.now(),
                )
        except HttpError as exc:
            if exc.status != 404:
                return self.lookup_failed(intent, actor)
            result = ReconciliationResult(status="MISSING", checked_at=self.clock.now())
        except Exception:
            return self.lookup_failed(intent, actor)
        return self.view(self.store.reconcile(intent, result, actor))

    def reconcile_unknown(self, intent, actor: str = "system-dispatcher") -> dict:
        try:
            found = self.erp.find_by_idempotency_key(intent.idempotency_key)
            records = self.erp.find_by_business_key(intent.business_key)
        except Exception:
            return self.lookup_failed(intent, actor)
        if len(records) > 1:
            result = ReconciliationResult(status="DUPLICATE", checked_at=self.clock.now())
            return self.view(self.store.reconcile(intent, result, actor))
        if found is None and records:
            old = records[0]
            # A pre-commit timeout on UPDATE may leave the previous verified target intact.
            unchanged_update = (
                intent.operation == "UPDATE_DRAFT"
                and old.version == intent.expected_target_version
                and old.ref.external_id == intent.external_id
            )
            if not unchanged_update:
                found = old
        if found is not None:
            if canonical_hash(found.payload) != intent.payload_sha256:
                result = ReconciliationResult(
                    status="DIFF",
                    diffs=compare_payload(intent.payload, found.payload),
                    checked_at=self.clock.now(),
                    target_version=found.version,
                )
                return self.view(self.store.reconcile(intent, result, actor))
            with self.repo.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = self.store.get(intent.id, conn)
                if current.state != "UNKNOWN_OUTCOME" or current.updated_at != intent.updated_at:
                    return self.view(current)
                current.receipt, current.external_id = self.receipt(found), found.ref.external_id
                current.state, current.updated_at = "ACKED", self.clock.now()
                self.store.save(conn, current)
                self.store.event(conn, current, "sync_unknown_found", actor)
            return self.verify(current, actor)
        if (
            self.erp.lookup_consistency == "eventual"
            and (self.clock.now() - intent.updated_at).total_seconds() < self.erp.settle_seconds
        ):
            return self.view(intent)
        state = "QUARANTINED" if intent.attempts >= intent.max_attempts else "PREPARED"
        return self.view(self.transition(intent, state, actor, "TARGET_NOT_FOUND"))

    def lookup_failed(self, intent, actor: str) -> dict:
        """Bound failed confirmation reads without retrying an uncertain write."""
        with self.repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.store.get(intent.id, conn)
            if current.state != intent.state or current.updated_at != intent.updated_at:
                return self.view(current)
            rows = conn.execute(
                "SELECT detail FROM events WHERE run_id=? AND action='sync_lookup_failed'",
                (intent.run_id,),
            ).fetchall()
            import json

            failures = (
                sum(json.loads(row["detail"]).get("intent_id") == intent.id for row in rows) + 1
            )
            if failures >= current.max_attempts and current.state != "VERIFIED":
                current.state = "QUARANTINED"
            current.last_error_class, current.last_error_code = "unknown", "LOOKUP_UNAVAILABLE"
            self.store.save(conn, current)
            self.store.event(
                conn, current, "sync_lookup_failed", actor, confirmation_failures=failures
            )
        return self.view(self.store.get(intent.id))

    def reconcile(self, identifier: str, actor: str = "system-dispatcher") -> dict:
        self.recover(identifier, actor)
        intent = self.store.get(identifier)
        if intent.state == "UNKNOWN_OUTCOME":
            return self.reconcile_unknown(intent, actor)
        return self.verify(intent, actor)

    def reconcile_sweep(
        self, limit: int = 20, triggered_by: str = "system-dispatcher"
    ) -> list[dict]:
        results = []
        for intent in self.store.list():
            if len(results) >= limit:
                break
            if intent.state in {"DISPATCHING", "UNKNOWN_OUTCOME", "ACKED", "VERIFIED"}:
                results.append(self.reconcile(intent.id, triggered_by))
            elif intent.state in ACTIVE_SEND:
                results.append(self.dispatch_once(intent.id, triggered_by))
        return results

    def resolve(self, identifier: str, decision: str, note: str, actor: str) -> dict:
        intent = self.store.get(identifier)
        if intent.state not in {"DIVERGED", "QUARANTINED", "HELD"}:
            raise DomainError("SYNC_PRECONDITION_FAILED", "当前状态无需人工决议。", 409)
        if decision == "rebase":
            body = self.repo.get(intent.run_id)
            targets = self.erp.find_by_business_key(intent.business_key)
            if len(targets) > 1:
                raise DomainError("TARGET_DIVERGED", "请先人工核对目标中的重复草案。", 409)
            target = targets[0] if targets else None

            def invalidate(current, conn):
                from rfq_workbench.service import clear_results

                clear_results(current)
                current["state"] = "VALIDATING"
                current_intent = self.store.get(identifier, conn)
                if (
                    current_intent.state != intent.state
                    or current_intent.updated_at != intent.updated_at
                ):
                    raise DomainError("VERSION_CONFLICT", "同步意图已更新，请刷新。", 409)
                current_intent.state, current_intent.updated_at = "SUPERSEDED", self.clock.now()
                if target:
                    current_intent.external_id = target.ref.external_id
                    current_intent.expected_target_version = target.version
                    if current_intent.receipt is None:
                        current_intent.receipt = self.receipt(target)
                self.store.save(conn, current_intent)
                return {"intent_id": identifier, "decision": decision, "note": note}

            self.repo.update_tx(intent.run_id, body["version"], actor, "sync_resolved", invalidate)
        else:
            with self.repo.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = self.store.get(identifier, conn)
                if current.state != intent.state or current.updated_at != intent.updated_at:
                    raise DomainError("VERSION_CONFLICT", "同步意图已更新，请刷新。", 409)
                current.state, current.updated_at = "SUPERSEDED", self.clock.now()
                self.store.save(conn, current)
                self.store.event(
                    conn, current, "sync_resolved", actor, note=note, decision=decision
                )
        return self.view(self.store.get(identifier))
