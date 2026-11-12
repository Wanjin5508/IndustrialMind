"""Regressions for the approval and file-delivery gaps found in the M6 audit."""

import json

import pytest
from conftest import APPROVER, ENGINEER, ROOT, approve, download_draft
from test_files import analyze, cost, fixture_run

from rfq_workbench.costing import fingerprint
from rfq_workbench.service import input_hash


@pytest.mark.parametrize(
    ("field", "value"), [("drawing.revision", "B"), ("rfq.required_revision", "A")]
)
def test_revision_override_preserves_block_and_correct_file_unblocks(client, field, value):
    run = analyze(client, fixture_run(client, "DEV-003"))
    path = f"/runs/{run['id']}"
    response = client.patch(
        path + "/fields",
        headers=ENGINEER,
        json={
            "expected_version": run["version"],
            "values": {field: value},
            "reason": "Reason only",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_OVERRIDE"
    assert client.get(path, headers=ENGINEER).json() == run
    assert cost(client, run).status_code == 422

    drawing = (ROOT / "fixtures/dev/drawings/PM-BRKT-010_B.pdf").read_bytes()
    replaced = client.post(
        path + "/drawing",
        headers=ENGINEER,
        data={"expected_version": run["version"], "reason": "Replace with the RFQ revision"},
        files={"drawing": ("drawing-B.pdf", drawing)},
    )
    assert replaced.status_code == 200
    verified = analyze(client, replaced.json())
    assert verified["fields"]["drawing.revision"]["value"] == "B"
    assert cost(client, verified).json()["state"] == "READY_FOR_APPROVAL"


def test_pre_fix_revision_override_cannot_download_even_with_matching_hashes(client):
    valid = approve(client, cost(client, analyze(client, fixture_run(client))).json())
    old = analyze(client, fixture_run(client, "DEV-003"))
    repo = client.app.state.workflow.repo

    def legacy_export(body):
        body["fields"]["drawing.revision"].update(value="B", human_confirmed=True)
        body["inputs"] = valid["inputs"]
        body["issues"] = []
        body["quote"] = {**valid["quote"], "input_hash": input_hash(body)}
        body["approval"] = {**valid["approval"], "quote_hash": fingerprint(body["quote"])}
        body["state"] = "EXPORTED_DRAFT"
        body["export"] = {
            "filename": "legacy-draft.md",
            "markdown": "Pre-fix export with unchanged A drawing",
            "approval": body["approval"],
            "quote": body["quote"],
        }
        return {}

    old = repo.update(old["id"], old["version"], "demo-approver", "legacy_export", legacy_export)
    blocked = download_draft(client, old)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "STALE_DRAFT"
    assert (
        client.get(f"/runs/{old['id']}/integration-preview", headers=APPROVER).json()["erp"] is None
    )


@pytest.mark.parametrize("dependency", ["rate", "blank"])
def test_download_checks_current_dependencies_without_leaking_export(client, settings, dependency):
    run = approve(client, cost(client, analyze(client, fixture_run(client))).json())
    path = f"/runs/{run['id']}"
    assert download_draft(client, run).json()["error"]["code"] == "NO_EXPORT"
    run = client.post(
        path + "/export", headers=APPROVER, json={"expected_version": run["version"]}
    ).json()
    assert run["state"] == "EXPORTED_DRAFT" and set(run["export"]) == {"filename"}
    assert download_draft(client, run, headers=ENGINEER).status_code == 403
    attachment = download_draft(client, run, "json")
    assert attachment.status_code == 200
    assert "attachment" in attachment.headers["content-disposition"]
    assert "no-store" in attachment.headers["cache-control"]
    row = attachment.json()["quote"]["material_lines"][0]
    assert row == {
        "code": "BLANK-PM-BRKT-010",
        "specification": "82 × 52 × 13 mm",
        "quantity_per_part": 1,
        "unit": "piece",
        "material": "Al-6061-T6",
        "batch_quantity": 100,
        "template_version": "bracket-routing-demo-2",
        "catalog_version": "catalog-demo-1",
    }
    assert attachment.json()["kind"] == "INTERNAL_DRAFT"
    assert attachment.json()["quote"]["total_price"] == "2547.00"
    assert "100 piece/batch" in download_draft(client, run).text

    ref = json.loads(settings.reference_path.read_text("utf-8"))
    if dependency == "rate":
        ref["machine_hourly"] = "61.00"
    else:
        ref["blank"]["specification"] = "83 × 53 × 14 mm"
    settings.reference_path.write_text(json.dumps(ref), encoding="utf-8")
    detail = client.get(path, headers=APPROVER).json()
    listed = next(r for r in client.get("/runs", headers=ENGINEER).json() if r["id"] == run["id"])
    assert detail == run and listed["export"] == run["export"]
    for snapshot, format in ((run, "md"), (detail, "json")):
        denied = download_draft(client, snapshot, format)
        assert denied.status_code == 409
        assert denied.json()["error"]["code"] == "STALE_DRAFT"
