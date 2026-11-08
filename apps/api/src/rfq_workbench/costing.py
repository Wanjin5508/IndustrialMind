"""Deterministic internal budget calculation with versioned reference data."""

import hashlib
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from rfq_workbench.contracts import ConfirmedInputs, CostLine, Issue, Quote
from rfq_workbench.errors import DomainError


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def load_reference(path: Path, today: date | None = None) -> tuple[dict, str]:
    """Reject invalid/expired rates before calculation, approval, and export."""
    try:
        raw = path.read_bytes()
        ref = json.loads(raw)
        now = today or date.today()
        if ref["approved"] is not True or not date.fromisoformat(
            ref["valid_from"]
        ) <= now <= date.fromisoformat(ref["valid_until"]):
            raise ValueError("Unapproved or expired reference")
        for key in [
            "rules_version",
            "rate_version",
            "template_version",
            "plant",
            "product_family",
            "material",
            "unit",
            "currency",
        ]:
            if not isinstance(ref[key], str) or not ref[key].strip():
                raise ValueError("Missing reference metadata")
        if not isinstance(ref["pending"], list) or not all(
            isinstance(item, str) for item in ref["pending"]
        ):
            raise ValueError("Invalid pending conditions")
        for key in [
            "material_unit",
            "machine_hourly",
            "cycle_hours",
            "setup_hours",
            "finish_unit",
            "inspection_hourly",
            "inspection_hours",
            "target_margin",
        ]:
            number = Decimal(ref[key])
            if not number.is_finite() or number < 0:
                raise ValueError("Invalid decimal")
        if not Decimal(0) <= Decimal(ref["target_margin"]) < Decimal(1):
            raise ValueError("Invalid margin")
        return ref, hashlib.sha256(raw).hexdigest()
    except (OSError, KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise DomainError(
            "INVALID_RATE", "Reference data is missing, invalid, unapproved or expired."
        ) from exc


def validate_inputs(inputs: ConfirmedInputs, ref: dict) -> list[Issue]:
    issues = []
    if inputs.required_revision != inputs.drawing_revision:
        issues.append(
            Issue(
                code="REVISION_CONFLICT",
                field="drawing_revision",
                message="RFQ and drawing revisions differ. Confirm the applicable document.",
            )
        )
    for field, code in [
        ("material", "UNSUPPORTED_MATERIAL"),
        ("unit", "UNSUPPORTED_UNIT"),
        ("currency", "UNSUPPORTED_CURRENCY"),
        ("plant", "UNSUPPORTED_PLANT"),
        ("product_family", "UNSUPPORTED_FAMILY"),
    ]:
        if getattr(inputs, field) != ref[field]:
            issues.append(
                Issue(
                    code=code,
                    field=field,
                    message=f"No approved template for {field}={getattr(inputs, field)}.",
                )
            )
    return issues


def calculate(
    inputs: ConfirmedInputs, ref: dict, reference_hash: str, input_hash: str, draft_version: int
) -> Quote:
    issues = validate_inputs(inputs, ref)
    if issues:
        raise DomainError(issues[0].code, issues[0].message)

    def d(key: str) -> Decimal:
        return Decimal(ref[key])

    quantity = Decimal(inputs.quantity)
    specs = [
        ("material", "Blank material", d("material_unit"), "material_unit EUR/part"),
        (
            "machining",
            "Milling and drilling",
            d("cycle_hours") * d("machine_hourly"),
            f"{ref['cycle_hours']} h/part x {ref['machine_hourly']} EUR/h",
        ),
        ("finishing", "External finishing", d("finish_unit"), "finish_unit EUR/part"),
        (
            "inspection",
            "Inspection",
            d("inspection_hours") * d("inspection_hourly"),
            f"{ref['inspection_hours']} h/part x {ref['inspection_hourly']} EUR/h",
        ),
        (
            "setup",
            "Batch setup allocation",
            d("setup_hours") * d("machine_hourly") / quantity,
            f"{ref['setup_hours']} h/batch x {ref['machine_hourly']} EUR/h / {inputs.quantity} parts",
        ),
    ]
    lines = [
        CostLine(
            code=code, description=desc, unit_cost=cost, batch_cost=cost * quantity, basis=basis
        )
        for code, desc, cost, basis in specs
    ]
    unit_cost = sum((line.unit_cost for line in lines), Decimal(0))
    price = (unit_cost / (1 - d("target_margin"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Quote(
        draft_version=draft_version,
        quantity=inputs.quantity,
        unit_cost=unit_cost,
        unit_price=price,
        total_price=price * quantity,
        margin=d("target_margin"),
        lines=lines,
        input_hash=input_hash,
        reference_hash=reference_hash,
        rules_version=ref["rules_version"],
        rate_version=ref["rate_version"],
        template_version=ref["template_version"],
        pending=ref["pending"],
    )
