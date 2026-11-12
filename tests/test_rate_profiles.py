from dataclasses import replace

import pytest
from conftest import APPROVER, ENGINEER, approve
from fastapi.testclient import TestClient

from rfq_workbench import config
from rfq_workbench.config import Settings
from rfq_workbench.main import create_app


@pytest.fixture
def profile_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "load_dotenv", lambda _: None)
    monkeypatch.setenv("RFQ_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "RFQ_RATE_PROFILE",
        "RFQ_FAKE_SYSTEMS_PATH",
        "RFQ_SYNC_FAULT_PLAN",
        "RFQ_ENGINEER_TOKEN",
        "RFQ_APPROVER_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def analyzed_fixture(client):
    response = client.post("/runs/from-fixture/DEV-001", headers=ENGINEER)
    assert response.status_code == 201, response.text
    run = response.json()
    response = client.post(
        f"/runs/{run['id']}/analyze",
        headers=ENGINEER,
        json={"expected_version": run["version"], "mode": "local_text"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_rate_profiles_keep_independent_quotes_and_erp_records(profile_env, tmp_path):
    custom_fake_db = tmp_path / "erp.sqlite3"
    profile_env.setenv("RFQ_FAKE_SYSTEMS_PATH", str(custom_fake_db))
    baseline = Settings.from_env()
    profile_env.setenv("RFQ_RATE_PROFILE", "B")
    variant = Settings.from_env()
    assert baseline.data_dir != variant.data_dir
    assert baseline.systems_path == custom_fake_db
    assert baseline.systems_path != variant.systems_path

    for settings, amount, rate, version in (
        (baseline, "2547.00", "60.00", "rates-eur-demo-1"),
        (variant, "2817.00", "75.00", "rates-eur-demo-b-1"),
    ):
        app = create_app(settings)
        with TestClient(app) as client:
            assert client.get("/runs", headers=ENGINEER).json() == []
            run = analyzed_fixture(client)
            path = f"/runs/{run['id']}"
            response = client.post(
                f"{path}/cost", headers=ENGINEER, json={"expected_version": run["version"]}
            )
            assert response.status_code == 200, response.text
            run = response.json()
            assert run["quote"]["total_price"] == amount
            assert run["quote"]["rate_version"] == version
            run = approve(client, run)
            response = client.post(
                f"{path}/sync",
                headers=APPROVER | {"Idempotency-Key": "rate-profile-demo-001"},
                json={"expected_version": run["version"], "target": "ERP", "note": "Rate demo"},
            )
            assert response.status_code == 200, response.text
            intent = response.json()
            response = client.post(f"/sync/{intent['id']}/dispatch", headers=APPROVER)
            assert response.status_code == 200, response.text
            final = response.json()
            assert final["state"] == "VERIFIED"
            assert final["reconciliation"]["status"] == "MATCH"
            records = app.state.systems.erp.find_by_business_key(intent["business_key"])
            assert len(records) == 1
            assert records[0].payload["total_price"] == amount

            sync = client.get(f"{path}/sync", headers=ENGINEER).json()
            erp_snapshot = next(row for row in sync["snapshots"] if row["ref"]["system"] == "ERP")
            assert erp_snapshot["payload"]["machine_hourly"] == rate


def test_variant_requires_matching_approved_rate_version(profile_env):
    baseline = Settings.from_env()
    profile_env.setenv("RFQ_RATE_PROFILE", "B")
    variant = replace(Settings.from_env(), knowledge_path=baseline.knowledge_path)
    with TestClient(create_app(variant)) as client:
        run = analyzed_fixture(client)
        response = client.post(
            f"/runs/{run['id']}/cost", headers=ENGINEER, json={"expected_version": run["version"]}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "NO_UNIQUE_BASIS"


def test_unknown_rate_profile_is_rejected(profile_env):
    profile_env.setenv("RFQ_RATE_PROFILE", "C")
    with pytest.raises(ValueError, match="RFQ_RATE_PROFILE must be A or B"):
        Settings.from_env()
