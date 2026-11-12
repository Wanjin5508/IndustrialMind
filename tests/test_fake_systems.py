"""Persistent fake ports expose real deduplication, optimistic versions and faults."""

import pytest

from rfq_workbench.clock import FakeClock
from rfq_workbench.integrations.fakes import FakeSystems
from rfq_workbench.integrations.ports import HttpError, TransportError


def test_commit_timeout_reopens_as_one_draft_and_duplicate_delivery(settings):
    first = FakeSystems(settings, FakeClock())
    first.faults.add("erp.put_draft", "timeout_after_commit")
    with pytest.raises(TransportError):
        first.erp.put_draft("business", {"lines": [], "amount": "1.00"}, "key", None)
    reopened = FakeSystems(settings, FakeClock())
    assert reopened.count_drafts() == 1
    result = reopened.erp.find_by_idempotency_key("key")
    assert result.payload["amount"] == "1.00"
    repeated = reopened.erp.put_draft("business", result.payload, "key", None)
    assert repeated == result
    with pytest.raises(HttpError) as error:
        reopened.erp.put_draft("business", {"amount": "2.00"}, "new-key", None)
    assert error.value.status == 412
    assert reopened.count_drafts() == 1
    reopened.faults.add("erp.put_draft", "duplicate_delivery")
    updated = reopened.erp.put_draft("business", {"amount": "2.00"}, "new-key", "1")
    assert updated.version == "2" and reopened.count_drafts() == 1


def test_fake_plm_release_versions_and_schema_fault(settings):
    fake = FakeSystems(settings, FakeClock())
    raw = fake.plm.read_item_revision(fake.part, fake.plant)
    fake.plm.release_new_revision("C")
    assert fake.plm.probe_version(raw.ref) != raw.version
    fake.faults.add("plm.read_item_revision", "schema_drift")
    assert "part_number" not in fake.plm.read_item_revision(fake.part, fake.plant).payload
    assert fake.plm.read_item_revision(fake.part, fake.plant).payload["part_number"] == fake.part
