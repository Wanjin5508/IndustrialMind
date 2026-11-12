"""Fault injection checks target effects, fencing and send preconditions."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from m9_scenarios import scenario  # noqa: E402


@pytest.mark.parametrize("number", [*range(1, 16), 17])
def test_delivery_scenarios(tmp_path, number):
    result = scenario(f"M9-{number:02d}", tmp_path)
    expected = {
        1: ("VERIFIED", 1),
        2: ("VERIFIED", 1),
        3: ("VERIFIED", 1),
        4: ("VERIFIED", 1),
        5: ("VERIFIED", 1),
        6: ("VERIFIED", 1),
        7: ("VERIFIED", 1),
        8: ("DIVERGED", 1),
        9: ("QUARANTINED", 0),
        10: ("HELD", 0),
        11: ("SUPERSEDED", 0),
        12: ("DIVERGED", 1),
        13: ("BLOCKED", 0),
        14: ("VERIFIED", 1),
        15: ("VERIFIED", 1),
        17: ("DIVERGED", 2),
    }
    assert (result["state"], result["draft_count"]) == expected[number]
