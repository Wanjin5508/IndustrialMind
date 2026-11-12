"""Reproduce the three-year RFQ capacity scenarios using the documented assumptions."""

import json
from dataclasses import dataclass
from decimal import Decimal

# Scenario brief supplies annual RFQ volume. Other parameters are planning assumptions.
RFQS_PER_YEAR = 15_000
ADOPTION = tuple(map(Decimal, ("0.25", "0.60", "0.80")))
BASELINE_HOURS = Decimal("2.00")
EUROS_PER_HOUR = 65
HOURS_PER_FTE = 1_600
INITIAL_EUR = 100_000
ANNUAL_EUR = 80_000


@dataclass(frozen=True)
class Scenario:
    eligible_share: Decimal
    assisted_hours: Decimal
    capacity_reuse: Decimal


SCENARIOS = {
    "conservative": Scenario(Decimal("0.40"), Decimal("1.50"), Decimal("0.50")),
    "base": Scenario(Decimal("0.60"), Decimal("1.25"), Decimal("0.75")),
    "optimistic": Scenario(Decimal("0.75"), Decimal("1.00"), Decimal("0.85")),
}


def project(scenario: Scenario) -> dict:
    hours_saved_per_rfq = BASELINE_HOURS - scenario.assisted_hours
    annual = []
    cumulative = Decimal(0)
    payback_month = None
    for year, adoption in enumerate(ADOPTION, start=1):
        processed = RFQS_PER_YEAR * scenario.eligible_share * adoption
        saved_hours = processed * hours_saved_per_rfq
        value = saved_hours * EUROS_PER_HOUR * scenario.capacity_reuse
        initial = INITIAL_EUR if year == 1 else 0
        cost = initial + ANNUAL_EUR
        net = value - cost
        # Upfront spend occurs at month zero. Recurring cost and value accrue uniformly.
        opening = cumulative - initial
        if payback_month is None and opening < 0 <= cumulative + net:
            payback_month = Decimal((year - 1) * 12) + (-opening * 12) / (value - ANNUAL_EUR)
        cumulative += net
        annual.append(
            {
                "year": year,
                "adopted_rfqs": processed,
                "hours_released": saved_hours,
                "capacity_value_eur": value,
                "cost_eur": cost,
                "net_capacity_value_eur": net,
                "cumulative_net_eur": cumulative,
            }
        )

    total_cost = sum(row["cost_eur"] for row in annual)
    total_value = sum(row["capacity_value_eur"] for row in annual)
    total_rfqs = sum(row["adopted_rfqs"] for row in annual)
    year_3_hours = annual[-1]["hours_released"]
    return {
        "annual": annual,
        "effort_reduction_pct": 100 * hours_saved_per_rfq / BASELINE_HOURS,
        "total_value_eur": total_value,
        "total_cost_eur": total_cost,
        "total_net_eur": total_value - total_cost,
        "roi_pct": 100 * (total_value - total_cost) / total_cost,
        "year_3_raw_fte": year_3_hours / HOURS_PER_FTE,
        "year_3_reusable_fte": year_3_hours * scenario.capacity_reuse / HOURS_PER_FTE,
        "break_even_minutes_per_rfq": 60
        * total_cost
        / (total_rfqs * EUROS_PER_HOUR * scenario.capacity_reuse),
        "capacity_payback_month": payback_month,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            {name: project(scenario) for name, scenario in SCENARIOS.items()}, default=str, indent=2
        )
    )
