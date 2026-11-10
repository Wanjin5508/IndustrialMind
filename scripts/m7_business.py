"""Black-box business and fault checks for isolated M7 cases."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient
from httpx import Response
from m7_suite import read_json

from rfq_workbench.config import Settings
from rfq_workbench.main import create_app

ENGINEER = {"X-Demo-Token": "demo-engineer"}
APPROVER = {"X-Demo-Token": "demo-approver"}
APPROVAL = {
    "technical_confirmed": True,
    "internal_only": True,
    "note": "Synthetic M7 technical review",
}
COST_CODES = ("material", "machining", "finishing", "inspection", "setup")


def result(response: Response) -> dict:
    """Record stable status and domain error code, without provider error bodies."""
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    return {"status": response.status_code, "code": body.get("error", {}).get("code")}


def require(response: Response, step: str) -> dict:
    if not response.is_success:
        raise RuntimeError(f"{step}: {result(response)}")
    return response.json()


def upload(client: TestClient, folder: Path, rfq: Path | None = None) -> dict:
    rfq = rfq or folder / "rfq.txt"
    return require(
        client.post(
            "/runs/upload",
            headers=ENGINEER,
            files={
                "rfq": (rfq.name, rfq.read_bytes()),
                "drawing": ("drawing.pdf", (folder / "drawing.pdf").read_bytes()),
            },
        ),
        "upload",
    )


def path(run: dict) -> str:
    return f"/runs/{run['id']}"


def read(client: TestClient, run: dict) -> dict:
    return require(client.get(path(run), headers=ENGINEER), "read run")


def mutate(client: TestClient, run: dict, action: str, headers=ENGINEER, **fields) -> Response:
    return client.post(
        f"{path(run)}/{action}",
        headers=headers,
        json={"expected_version": run["version"], **fields},
    )


def download(client: TestClient, run: dict, headers=APPROVER, format="json") -> Response:
    return client.get(
        path(run) + "/export/download",
        headers=headers,
        params={"expected_version": run["version"], "format": format},
    )


def correct(client: TestClient, run: dict, field: str, value: str, reason: str) -> Response:
    return client.patch(
        path(run) + "/fields",
        headers=ENGINEER,
        json={
            "expected_version": run["version"],
            "values": {field: value},
            "reason": reason,
        },
    )


def no_draft(run: dict) -> bool:
    return all(run[k] is None for k in ("quote", "approval", "export"))


def knowledge_checks(client: TestClient, case: str, gold: dict, reference: Path) -> dict:
    approved = read_json(reference)
    query = {
        "part_number": approved["applicability"]["part_number"],
        "revision": approved["approved_revisions"][0],
        **{key: approved[key] for key in ("material", "unit", "currency")},
    }
    rows = require(client.post("/knowledge/search", headers=ENGINEER, json=query), "search")[
        "results"
    ]
    by_id = {row["id"]: row for row in rows}
    history = by_id.get(case + "-HISTORY", {})
    rate = by_id.get(case + "-RATE", {})
    eligible = {row["kind"] for row in rows if row["eligible_for_cost"]}
    # Query using the requested revision and approved unit. Drawing conflicts are tested
    # independently; the archive must still be excluded from the price basis.
    expected = {"catalog", "template"} if case == "F12" else {"catalog", "template", "rate"}
    return {
        "eligible_kinds": sorted(eligible),
        "archive_reasons": history.get("reasons", []),
        "rate_reasons": rate.get("reasons", []),
        "checks": {
            "correct_eligible_set": eligible == expected,
            "inapplicable_history": history.get("eligible_for_cost") is False
            and {"REFERENCE_ONLY", "MISMATCH_PART_NUMBER", "MISMATCH_REVISION"}
            <= set(history["reasons"]),
            "private_filtered": case + "-PRIVATE" not in by_id
            and client.get(f"/knowledge/{case}-PRIVATE", headers=ENGINEER).status_code == 404,
            "unauthenticated_denied": client.post("/knowledge/search", json=query).status_code
            == 401,
            "rate_reason": not gold.get("block", {}).get("knowledge_reason")
            or gold["block"]["knowledge_reason"] in rate.get("reasons", []),
        },
    }


def quote_checks(client: TestClient, run: dict, case: str, gold: dict, settings: Settings) -> dict:
    start = perf_counter()
    response = mutate(client, run, "cost")
    result_row = {"cost": result(response), "checks": {}}
    checks = result_row["checks"]
    if block := gold.get("block"):
        observed = {(i["code"], i["field"]) for i in run["issues"]}
        checks["expected_issue"] = (
            "issue" not in block or (block["issue"], block["field"]) in observed
        )
        checks["cost_blocked"] = result(response) == {"status": 422, "code": block["cost_error"]}
        after = read(client, run)
        checks["approval_and_download_blocked"] = (
            mutate(client, after, "approve", APPROVER, **APPROVAL).status_code >= 400
            and download(client, after).status_code >= 400
            and no_draft(read(client, run))
        )
    elif response.is_success:
        draft = response.json()
        quote, want = draft["quote"], gold["quote"]
        material = quote["material_lines"]
        result_row["amounts"] = {key: quote[key] for key in ("unit_price", "total_price")}
        checks["price"] = all(Decimal(quote[k]) == Decimal(want[k]) for k in result_row["amounts"])
        checks["five_cost_components"] = [row["code"] for row in quote["lines"]] == list(
            COST_CODES
        ) and [Decimal(row["batch_cost"]).quantize(Decimal("0.01")) for row in quote["lines"]] == [
            Decimal(v) for v in want["batch_costs"]
        ]
        checks["blank"] = len(material) == 1 and (
            material[0]["code"],
            material[0]["batch_quantity"],
            material[0]["quantity_per_part"],
        ) == (want["blank_code"], want["batch_blanks"], 1)
        checks["controlled_basis"] = {r["source_id"] for r in quote["knowledge_basis"]} == {
            f"{case}-CATALOG",
            f"{case}-TEMPLATE",
            f"{case}-RATE",
        }
        checks["no_approval_before_user"] = (
            not run["issues"]
            and draft["approval"] is None
            and draft["export"] is None
            and mutate(client, draft, "approve", ENGINEER, **APPROVAL).status_code == 403
            and mutate(client, draft, "export", APPROVER).status_code >= 400
        )
        approval_started = perf_counter()
        approved = require(mutate(client, draft, "approve", APPROVER, **APPROVAL), "approve")
        result_row["scripted_approval_api_ms"] = round(
            (perf_counter() - approval_started) * 1000, 2
        )
        exported = require(mutate(client, approved, "export", APPROVER), "export")
        result_row["cost_through_export_ms"] = round((perf_counter() - start) * 1000, 2)
        attached = download(client, exported)
        markdown = download(client, exported, format="md")
        checks["approval_and_download"] = (
            approved["state"] == "APPROVED_DRAFT"
            and exported["state"] == "EXPORTED_DRAFT"
            and set(exported["export"]) == {"filename"}
            and download(client, exported, ENGINEER).status_code == 403
            and attached.is_success
            and attached.json()["kind"] == "INTERNAL_DRAFT"
            and attached.json()["quote"] == quote
            and attached.json()["approval"] == approved["approval"]
            and markdown.is_success
            and want["total_price"] in markdown.text
        )
        duplicate = mutate(client, exported, "export", APPROVER)
        checks["idempotent_export"] = duplicate.is_success and duplicate.json() == exported
        original = settings.reference_path.read_bytes()
        try:
            settings.reference_path.write_bytes(
                original.replace(b'"rules_version": "m7-quote-1"', b'"rules_version": "m7-stale"')
            )
            checks["stale_reference_download_denied"] = result(download(client, exported)) == {
                "status": 409,
                "code": "STALE_DRAFT",
            }
        finally:
            settings.reference_path.write_bytes(original)
        changed = require(
            correct(
                client,
                exported,
                "rfq.quantity",
                str(quote["quantity"] + 1),
                "Synthetic quantity change after approval",
            ),
            "change quantity",
        )
        checks["quantity_invalidates_approval"] = no_draft(changed) and (
            download(client, changed).status_code >= 400
            and download(client, exported).status_code >= 400
        )
    else:
        checks["normal_case_reaches_cost"] = False
    result_row["check_wall_ms"] = round((perf_counter() - start) * 1000, 2)
    return result_row


def review_checks(client: TestClient, run: dict, case: str, gold: dict, addendum: Path) -> dict:
    if case == "F05":
        denied = correct(client, run, "drawing.revision", "D", "Attempt to override the original")
        return {
            "checks": {
                "revision_cannot_be_overridden": result(denied)
                == {"status": 409, "code": "REVISION_OVERRIDE"}
                and read(client, run) == run
            }
        }
    if case != "F09":
        return {}
    quantity = addendum.read_text("utf-8").split("Quantity: ")[1].strip()
    start = perf_counter()
    corrected = require(
        correct(client, run, "rfq.quantity", quantity, "Read synthetic buyer addendum DP-354"),
        "record quantity review",
    )
    patch_ms = round((perf_counter() - start) * 1000, 2)
    costed = require(mutate(client, corrected, "cost"), "cost reviewed input")
    field = corrected["fields"]["rfq.quantity"]
    expected = gold["recovered"]
    return {
        "scripted_review_patch_api_ms": patch_ms,
        "scripted_review_through_cost_ms": round((perf_counter() - start) * 1000, 2),
        "checks": {
            "audit_preserves_missing_original": field["original_value"] is None
            and field["human_confirmed"]
            and field["correction"]["actor"] == "demo-engineer"
            and field["value"] == quantity,
            "review_recovers_correct_quote": not corrected["issues"]
            and all(Decimal(costed["quote"][k]) == Decimal(v) for k, v in expected.items()),
        },
    }


def timeout_check(settings: Settings, folder: Path) -> dict:
    simulated = replace(settings, simulate_model_timeout=True)
    with TestClient(create_app(simulated)) as first:
        run = upload(first, folder)
        statuses = [result(mutate(first, run, "analyze", mode="live"))]
    with TestClient(create_app(simulated)) as restarted:
        for _ in range(2):
            run = read(restarted, run)
            statuses.append(result(mutate(restarted, run, "analyze", mode="live")))
        failed = read(restarted, run)
        rejected = result(mutate(restarted, failed, "analyze", mode="live"))
        resumed = require(mutate(restarted, failed, "analyze", mode="local_text"), "local recovery")
        return {
            "attempts": statuses,
            "fourth": rejected,
            "checks": {
                "bounded_timeout_and_restart": statuses
                == [{"status": 504, "code": "MODEL_TIMEOUT"}] * 3
                and rejected == {"status": 409, "code": "RETRY_LIMIT"}
                and failed["state"] == "FAILED_FINAL"
                and no_draft(failed)
                and failed["metrics"]["real_model_call"] is False,
                "explicit_offline_recovery": resumed["state"] == "VALIDATING"
                and resumed["mode"] == "LOCAL_TEXT"
                and resumed["metrics"]["real_model_call"] is False,
            },
        }
