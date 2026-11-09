"""Auditable token-based estimate. Unknown usage/pricing stays unknown, never zero."""

import hashlib
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import urlsplit

from rfq_workbench.config import Settings


def estimate_cost(
    settings: Settings, model: str, usage: dict | None, today: date | None = None
) -> dict:
    unknown = {
        "estimated_cost": None,
        "cost_note": "No matching valid tariff and complete usage; cost unknown.",
    }
    if usage is None:
        return unknown
    try:
        raw = settings.model_pricing_path.read_bytes()
        tariff = json.loads(raw)
        now = today or date.today()
        if (
            tariff["model"] != model
            or tariff["endpoint_host"] != urlsplit(settings.model_base_url).hostname
        ):
            return unknown
        if (
            not date.fromisoformat(tariff["verified_on"])
            <= now
            <= date.fromisoformat(tariff["review_after"])
        ):
            return unknown
        incoming, outgoing = usage["input_tokens"], usage["output_tokens"]
        cached = usage["input_tokens_details"]["cached_tokens"]
        if (
            any(type(v) is not int or v < 0 for v in (incoming, outgoing, cached))
            or cached > incoming
        ):
            return unknown
        rates = [
            Decimal(tariff[key])
            for key in (
                "input_uncached_per_million",
                "input_cached_per_million",
                "output_per_million",
            )
        ]
        if not all(v.is_finite() and v >= 0 for v in rates):
            return unknown
        amount = (
            (incoming - cached) * rates[0] + cached * rates[1] + outgoing * rates[2]
        ) / Decimal(1_000_000)
        return {
            "estimated_cost": str(amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)),
            "cost_currency": tariff["currency"],
            "cost_note": tariff["basis"],
            "pricing": tariff | {"sha256": hashlib.sha256(raw).hexdigest()},
        }
    except (OSError, KeyError, TypeError, ValueError, InvalidOperation):
        return unknown
