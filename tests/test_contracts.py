"""M0 input boundaries and source provenance tests."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rfq_workbench.contracts import ConfirmedInputs

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "100", True, 100_001])
def test_quantity_must_be_bounded_positive_integer(quantity):
    with pytest.raises(ValidationError):
        ConfirmedInputs(
            part_number="PM-BRKT-010",
            required_revision="B",
            drawing_revision="B",
            quantity=quantity,
            material="Al-6061-T6",
        )


def test_fixture_manifest_resolves_only_development_inputs():
    manifest = json.loads((ROOT / "fixtures/manifests/dev.json").read_text())
    assert len(manifest["cases"]) == 6
    for case in manifest["cases"]:
        assert case["origin_type"] == "synthetic"
        assert case["split"] == "dev"
        for kind in ["rfq", "drawing"]:
            path = ROOT / case[kind]
            assert path.is_relative_to(ROOT / "fixtures/dev")
            assert hashlib.sha256(path.read_bytes()).hexdigest() == case["hashes"][kind]


def test_gold_is_separate_from_runtime_inputs():
    paths = list((ROOT / "fixtures/dev").rglob("*"))
    assert not any("expected" in p.name or "gold" in p.name for p in paths)
    assert (ROOT / "evals/gold/dev.json").exists()
