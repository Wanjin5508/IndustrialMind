"""Small read/write ports shared by fakes and future HTTP adapters."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from rfq_workbench.integrations.contracts import ExternalRef


@dataclass(frozen=True)
class RawRecord:
    ref: ExternalRef
    version: str
    as_of: datetime
    payload: dict


class TransportError(Exception):
    def __init__(self, kind: Literal["connect", "read_timeout", "reset"]):
        self.kind = kind
        super().__init__(kind)


class HttpError(Exception):
    def __init__(self, status: int, body: dict | None = None, retry_after: int = 0):
        self.status = status
        self.body = body or {}
        self.retry_after = retry_after
        super().__init__(str(status))


class PlmPort(Protocol):
    def read_item_revision(self, part_number: str, plant: str) -> RawRecord: ...
    def probe_version(self, ref: ExternalRef) -> str: ...


class ErpPort(Protocol):
    lookup_consistency: Literal["strong", "eventual"]
    settle_seconds: float
    not_processed_statuses: frozenset[int]

    def read_cost_reference(self, material: str, plant: str) -> RawRecord: ...
    def probe_version(self, ref: ExternalRef) -> str: ...
    def find_by_idempotency_key(self, key: str) -> RawRecord | None: ...
    def find_by_business_key(self, business_key: str) -> list[RawRecord]: ...
    def read_draft(self, external_id: str) -> RawRecord: ...
    def put_draft(
        self, business_key: str, payload: dict, idempotency_key: str, if_match: str | None
    ) -> RawRecord: ...


class MesPort(Protocol):
    def read_feedback(self, part_number: str, plant: str) -> RawRecord: ...
    def probe_version(self, ref: ExternalRef) -> str: ...
