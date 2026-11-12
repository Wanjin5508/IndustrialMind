"""Golden encoding keeps outbound hashes portable without changing quote hashes."""

import hashlib
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from rfq_workbench.canonical import canonical_hash, canonical_json
from rfq_workbench.costing import fingerprint


def test_canonical_gold_and_existing_hash_compatibility():
    value = {"price": Decimal("25.47"), "missing": None, "at": datetime(2026, 11, 10, tzinfo=UTC)}
    expected = '{"at":"2026-11-10T00:00:00+00:00","missing":null,"price":"25.47"}'
    assert canonical_json(value) == expected
    assert canonical_hash(value) == hashlib.sha256(expected.encode()).hexdigest()
    value["at"] = datetime(2026, 11, 10, 1, tzinfo=timezone(timedelta(hours=1)))
    assert canonical_json(value) == expected
    legacy = {"quantity": 100, "unit_price": "25.47", "missing": None}
    raw = b'{"missing":null,"quantity":100,"unit_price":"25.47"}'
    assert fingerprint(legacy) == canonical_hash(legacy) == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "value", [1.0, float("nan"), {"x": [0.1]}, Decimal("NaN"), datetime(2026, 1, 1)]
)
def test_nonportable_hash_inputs_are_rejected(value):
    with pytest.raises(ValueError):
        canonical_json(value)
