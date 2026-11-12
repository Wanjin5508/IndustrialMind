"""Canonical integration JSON with explicit decimal and UTC timestamp semantics."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal


def normalize(value: object) -> object:
    if value is None or type(value) in (bool, int) or isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite decimals are forbidden")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Integration timestamps require a timezone")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    raise ValueError("Unsupported canonical value; floats are forbidden")


def canonical_json(value: object) -> str:
    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
