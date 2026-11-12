"""Strict internal contracts for simulated source provenance and ERP delivery."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field

from rfq_workbench.contracts import Contract, Mutation


class ExternalRef(Contract):
    system: Literal["PLM", "ERP", "MES"]
    entity: str
    external_id: str
    plant: str


class SourceSnapshot(Contract):
    id: str
    run_id: str
    ref: ExternalRef
    source_version: str
    as_of: datetime
    fetched_at: datetime
    ttl_seconds: int = Field(gt=0)
    payload: dict
    payload_sha256: str
    adapter_version: str
    mapping_version: str
    unknown_fields: list[str] = Field(default_factory=list)


class IntentState(StrEnum):
    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    RETRY_WAIT = "RETRY_WAIT"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    ACKED = "ACKED"
    VERIFIED = "VERIFIED"
    DIVERGED = "DIVERGED"
    HELD = "HELD"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"
    SUPERSEDED = "SUPERSEDED"


class Receipt(Contract):
    external_id: str
    target_version: str
    acked_at: datetime
    response_sha256: str


class FieldDiff(Contract):
    field: str
    expected: str | None
    actual: str | None


class ReconciliationResult(Contract):
    status: Literal["MATCH", "DIFF", "MISSING", "DUPLICATE"]
    diffs: list[FieldDiff] = Field(default_factory=list)
    target_version: str | None = None
    checked_at: datetime


class OutboundIntent(Contract):
    model_config = ConfigDict(validate_assignment=True)
    id: str
    run_id: str
    target: Literal["ERP"] = "ERP"
    operation: Literal["CREATE_DRAFT", "UPDATE_DRAFT"]
    business_key: str
    idempotency_key: str
    payload: dict
    payload_sha256: str
    approved_quote_hash: str
    base_snapshot_ids: list[str]
    mapping_version: str
    external_id: str | None = None
    expected_target_version: str | None = None
    state: IntentState = IntentState.PREPARED
    attempts: int = 0
    max_attempts: int = Field(gt=0)
    next_attempt_at: datetime | None = None
    lease_until: datetime | None = None
    lease_token: str | None = None
    last_error_class: str | None = None
    last_error_code: str | None = None
    receipt: Receipt | None = None
    requested_by: str
    created_at: datetime
    updated_at: datetime


class SyncRequest(Mutation):
    target: Literal["ERP"]
    note: str = Field(min_length=3, max_length=1000)


class SyncIntentView(OutboundIntent):
    reconciliation: dict | None = None
    attempt_log: list[dict] = Field(default_factory=list)


class ResolveSync(Contract):
    decision: Literal["rebase", "abandon"]
    note: str = Field(min_length=3, max_length=1000)


class DispatchDue(Contract):
    limit: int = Field(default=20, ge=1, le=100)
