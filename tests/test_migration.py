"""Migration preserves persisted task/approval bytes and transaction rollback."""

import sqlite3

import pytest
from conftest import ENGINEER, create_and_cost

from rfq_workbench.repository import Repository


def test_v1_migration_keeps_existing_rows(client, settings, inputs):
    run = create_and_cost(client, inputs)
    path = settings.data_dir / "workbench.sqlite3"
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT * FROM runs").fetchall()
        events = conn.execute("SELECT * FROM events").fetchall()
        conn.execute("PRAGMA user_version=1")
    repo = Repository(path)
    assert repo.get(run["id"]) == run
    with repo.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert [tuple(row) for row in conn.execute("SELECT * FROM runs")] == before
        assert [tuple(row) for row in conn.execute("SELECT * FROM events")] == events


def test_shared_transaction_rolls_back_and_background_events_keep_version(client):
    run = client.post("/tasks", headers=ENGINEER, json={}).json()
    repo = client.app.state.workflow.repo

    def fail(body, conn):
        body["title"] = "must roll back"
        repo.append_event(conn, body["id"], "system-dispatcher", "test", {})
        raise ValueError("injected transaction failure")

    with pytest.raises(ValueError):
        repo.update_tx(run["id"], run["version"], "demo-engineer", "test", fail)
    assert repo.get(run["id"]) == run
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        repo.append_event(conn, run["id"], "system-dispatcher", "background", {})
    assert repo.get(run["id"])["version"] == run["version"]
