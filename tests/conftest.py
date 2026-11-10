"""Isolated service configuration; tests never use the developer's database/key."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rfq_workbench.config import Settings
from rfq_workbench.main import create_app

ROOT = Path(__file__).resolve().parents[1]
ENGINEER = {"X-Demo-Token": "demo-engineer"}
APPROVER = {"X-Demo-Token": "demo-approver"}


@pytest.fixture
def settings(tmp_path):
    ref = tmp_path / "reference.json"
    shutil.copyfile(ROOT / "fixtures/reference/manufacturing.json", ref)
    knowledge = tmp_path / "knowledge.json"
    shutil.copyfile(ROOT / "fixtures/reference/knowledge.json", knowledge)
    return Settings(data_dir=tmp_path / "data", reference_path=ref, knowledge_path=knowledge)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def inputs():
    return {
        "part_number": "PM-BRKT-010",
        "required_revision": "B",
        "drawing_revision": "B",
        "quantity": 100,
        "material": "Al-6061-T6",
        "unit": "mm",
        "currency": "EUR",
        "plant": "DE-DEMO",
        "product_family": "mounting-bracket",
    }


def create_and_cost(client, inputs):
    response = client.post("/runs/confirmed", json=inputs, headers=ENGINEER)
    assert response.status_code == 201, response.text
    run = response.json()
    response = client.post(
        f"/runs/{run['id']}/cost", json={"expected_version": run["version"]}, headers=ENGINEER
    )
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, run):
    response = client.post(
        f"/runs/{run['id']}/approve",
        json={
            "expected_version": run["version"],
            "technical_confirmed": True,
            "internal_only": True,
            "note": "Verified synthetic input and cost basis",
        },
        headers=APPROVER,
    )
    assert response.status_code == 200, response.text
    return response.json()


def download_draft(client, run, format="md", headers=APPROVER):
    return client.get(
        f"/runs/{run['id']}/export/download",
        headers=headers,
        params={"expected_version": run["version"], "format": format},
    )
