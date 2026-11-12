"""M0 input boundaries and source provenance tests."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rfq_workbench.contracts import ConfirmedInputs

ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_INPUTS = {
    "rfq": ROOT / "docs/04-项目介绍与展示/08-RFQ样例",
    "drawing": ROOT / "fixtures/dev/drawings",
}


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
    manifest = json.loads((ROOT / "fixtures/manifests/dev.json").read_text(encoding="utf-8"))
    assert len(manifest["cases"]) == 6
    for case in manifest["cases"]:
        assert case["origin_type"] == "synthetic"
        assert case["split"] == "dev"
        for kind, directory in DEVELOPMENT_INPUTS.items():
            path = (ROOT / case[kind]).resolve()
            assert path.is_relative_to(directory.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest() == case["hashes"][kind]


def test_gold_is_separate_from_runtime_inputs():
    paths = [p for directory in DEVELOPMENT_INPUTS.values() for p in directory.rglob("*")]
    assert not any("expected" in p.name or "gold" in p.name for p in paths)
    assert (ROOT / "evals/gold/dev.json").exists()
