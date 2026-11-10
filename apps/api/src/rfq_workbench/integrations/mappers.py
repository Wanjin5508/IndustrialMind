"""Versioned, pure ERP mapping with explicit half-up cent rounding."""

from decimal import ROUND_HALF_UP, Decimal

from rfq_workbench.canonical import canonical_hash
from rfq_workbench.contracts import ConfirmedInputs, Contract, Quote
from rfq_workbench.costing import fingerprint
from rfq_workbench.errors import DomainError

MAPPING_VERSION = "erp-draft-v1"
CENT = Decimal("0.01")
TOLERANCE = CENT


class ErpDraftPayload(Contract):
    business_key: str
    plant: str
    part_number: str
    revision: str
    quantity: int
    currency: str
    unit: str
    quantity_unit: str
    unit_price: str
    total_price: str
    lines: list[dict]
    x_run_id: str
    x_quote_hash: str
    x_idempotency_key: str
    x_mapping_version: str
    x_draft_version: int


def money(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def map_quote_to_erp_draft_v1(
    quote: dict, inputs: dict, run_id: str, approval: dict, operation: str = "CREATE_DRAFT"
) -> ErpDraftPayload:
    parsed, confirmed = Quote.model_validate(quote), ConfirmedInputs.model_validate(inputs)
    if (
        confirmed.currency != "EUR"
        or confirmed.unit != "mm"
        or confirmed.plant != "DE-DEMO"
        or parsed.currency != confirmed.currency
    ):
        raise DomainError("MAPPING_UNSUPPORTED", "当前币种、单位或工厂无法映射。")
    digest = fingerprint(quote)
    if approval.get("quote_hash") != digest:
        raise DomainError("SYNC_NOT_APPROVED", "请批准当前报价版本。", 409)
    key = canonical_hash(
        {
            "run_id": run_id,
            "target": "ERP",
            "operation": operation,
            "approved_quote_hash": digest,
            "mapping_version": MAPPING_VERSION,
        }
    )
    lines = [
        {
            "code": line.code,
            "description": line.description,
            "unit_cost": money(line.unit_cost),
            "batch_cost": money(line.batch_cost),
        }
        for line in parsed.lines
    ]
    total = sum((Decimal(line["batch_cost"]) for line in lines), Decimal(0))
    if abs(total - parsed.unit_cost * parsed.quantity) > TOLERANCE:
        raise DomainError("MAPPING_PRECISION", "成本行舍入差异超过 0.01 EUR。")
    if parsed.total_price != parsed.unit_price * parsed.quantity:
        raise DomainError("MAPPING_PRECISION", "报价总额与单价和数量不一致。")
    return ErpDraftPayload(
        business_key=f"{run_id}:ERP",
        plant=confirmed.plant,
        part_number=confirmed.part_number,
        revision=confirmed.drawing_revision,
        quantity=confirmed.quantity,
        currency=confirmed.currency,
        unit=confirmed.unit,
        quantity_unit="piece",
        unit_price=money(parsed.unit_price),
        total_price=money(parsed.total_price),
        lines=lines,
        x_run_id=run_id,
        x_quote_hash=digest,
        x_idempotency_key=key,
        x_mapping_version=MAPPING_VERSION,
        x_draft_version=parsed.draft_version,
    )
