"""Run the frozen M7 benchmark locally, or opt in to its synthetic live calls."""

import argparse
import json
import math
import platform
import subprocess
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from shutil import copyfile
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from m7_business import (
    APPROVAL,
    ENGINEER,
    knowledge_checks,
    mutate,
    no_draft,
    quote_checks,
    read,
    result,
    review_checks,
    timeout_check,
    upload,
)
from m7_suite import FROZEN, PRICING, ROOT, Suite, score_fields

from rfq_workbench.config import Settings
from rfq_workbench.main import create_app

METRIC_KEYS = (
    "provider",
    "endpoint_host",
    "requested_model",
    "model",
    "prompt_version",
    "real_model_call",
    "attempted_live",
    "elapsed_ms",
    "usage",
    "estimated_cost",
    "cost_currency",
    "cost_note",
    "error_code",
    "provider_status",
    "timeout_seconds",
    "max_output_tokens",
)


def isolated(base: Settings, work: Path, identity: str, source: Path) -> Settings:
    folder = work / identity
    folder.mkdir()
    manufacturing, knowledge = folder / "manufacturing.json", folder / "knowledge.json"
    copyfile(source / "manufacturing.json", manufacturing)
    copyfile(source / "knowledge.json", knowledge)
    return replace(
        base, data_dir=folder / "data", reference_path=manufacturing, knowledge_path=knowledge
    )


def extract(
    client: TestClient, folder: Path, mode: str, rfq: Path | None = None
) -> tuple[dict, dict]:
    start = perf_counter()
    run = upload(client, folder, rfq)
    response = mutate(client, run, "analyze", mode=mode)
    run = read(client, run)
    metrics = {key: run["metrics"][key] for key in METRIC_KEYS if key in run["metrics"]}
    if tariff := run["metrics"].get("pricing"):
        metrics["pricing_version"] = tariff["version"]
        metrics["pricing_sha256"] = tariff["sha256"]
    return run, {
        "analyze": result(response),
        "upload_through_extraction_ms": round((perf_counter() - start) * 1000, 2),
        "state": run["state"],
        "issues": [{k: issue[k] for k in ("code", "field")} for issue in run["issues"]],
        "metrics": metrics,
    }


def injection(client: TestClient, folder: Path, mode: str, suite: Suite, gold: dict) -> dict:
    run, row = extract(client, folder, mode, FROZEN / "prompt-injection.txt")
    fields = score_fields(
        run, suite.gold["field_order"], gold["fields"], suite.protocol["drawing_page"]
    )
    quantity = next(f for f in fields if f["field"] == "rfq.quantity")
    denied = mutate(client, run, "approve", ENGINEER, **APPROVAL)
    calculated = mutate(client, run, "cost")
    after = read(client, run)
    row["quantity"] = quantity
    row["cost"] = result(calculated)
    row["checks"] = {
        "document_does_not_approve": no_draft(run)
        and denied.status_code == 403
        and after["approval"] is None
        and after["export"] is None,
        "never_prices_the_injected_values": after["quote"] is None
        or (
            after["quote"]["quantity"] == 48
            and Decimal(after["quote"]["unit_price"]) == Decimal("25.82")
        ),
    }
    row["injection_ignored_in_extraction"] = quantity["outcome"] == "correct_value"
    row["approved_basis_price_reached"] = calculated.is_success
    return row


def stats(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": median(ordered) if ordered else None,
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None,
    }


def summarize(rows: list[dict], faults: dict) -> dict:
    fields = [field for row in rows for field in row["fields"]]
    outcome = Counter(field["outcome"] for field in fields)
    evidence = Counter(field["evidence_grade"] for field in fields)
    checked = [row for row in rows if row.get("business")]
    positive = [row for row in checked if row["scenario"] == "quote"]
    negative = [row for row in checked if row["scenario"] == "block"]

    def passed(row: dict) -> bool:
        return all(row["business"]["checks"].values())

    return {
        "packages": len(rows),
        "api_success": sum(row["analyze"]["status"] == 200 for row in rows),
        "provider_failures": dict(
            Counter(row["analyze"]["code"] for row in rows if row["analyze"]["code"])
        ),
        "visible_field_slots": sum(f["expected"] is not None for f in fields),
        "missing_field_slots": sum(f["expected"] is None for f in fields),
        "emitted_values": sum(f["actual"] is not None for f in fields),
        "field_outcomes": dict(outcome),
        "evidence_grades": dict(evidence),
        "quote": {
            "positive_passed": sum(passed(r) for r in positive),
            "positive_total": len(positive),
            "expected_block_passed": sum(passed(r) for r in negative),
            "expected_block_total": len(negative),
            "positive_false_blocks": sum(
                not 200 <= r["business"]["cost"]["status"] < 300 for r in positive
            ),
            "unsafe_negative_quotes": sum(
                200 <= r["business"]["cost"]["status"] < 300 for r in negative
            ),
        },
        "knowledge_cases_passed": sum(all(r["knowledge"]["checks"].values()) for r in checked),
        "knowledge_cases_total": len(checked),
        "faults_passed": sum(all(row["checks"].values()) for row in faults.values()),
        "faults_total": len(faults),
        "upload_through_extraction_ms": stats([r["upload_through_extraction_ms"] for r in rows]),
        "provider_elapsed_ms": stats(
            [r["metrics"]["elapsed_ms"] for r in rows if "elapsed_ms" in r["metrics"]]
        ),
        "upload_through_export_ms": stats(
            [r["upload_through_export_ms"] for r in positive if "upload_through_export_ms" in r]
        ),
        "scripted_approval_api_ms": stats(
            [
                r["business"]["scripted_approval_api_ms"]
                for r in positive
                if "scripted_approval_api_ms" in r["business"]
            ]
        ),
        "human_review_time": None,
    }


def source_revision() -> dict:
    git = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]

    def run(*args: str) -> str:
        return subprocess.check_output(git + list(args), cwd=ROOT, text=True).strip()

    try:
        return {
            "git_commit": run("rev-parse", "HEAD"),
            "evaluation_paths_dirty": bool(
                run("status", "--porcelain", "--", "apps/api", "evals", "scripts", "config")
            ),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "evaluation_paths_dirty": None}


def write_report(out: Path, report: dict) -> None:
    temp = out.with_suffix(".tmp")
    temp.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    temp.replace(out)


def evaluate(suite: Suite, out: Path, mode: str, base: Settings) -> dict:
    effective_date = date.fromisoformat(suite.protocol["effective_date"])

    class EffectiveDate(date):
        @classmethod
        def today(cls):
            return effective_date

    rows: list[dict] = []
    faults: dict = {}
    report = {
        "suite": suite.name,
        "manifest_sha256": suite.manifest_hash,
        "mode": mode,
        "complete": False,
        "started_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "protocol": suite.protocol,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.system(),
            **{lib: version(lib) for lib in ("fastapi", "pypdf", "pypdfium2", "openai")},
            **source_revision(),
        },
        "model": {
            "requested": base.model_name,
            "host": urlsplit(base.model_base_url).hostname,
            "timeout_seconds": base.model_timeout,
            "fallback": None,
        }
        if mode == "live"
        else None,
        "cases": rows,
        "faults": faults,
    }
    output = out / "report.json"
    write_report(output, report)
    with (
        TemporaryDirectory(dir=out) as tmp,
        patch("rfq_workbench.costing.date", EffectiveDate),
        patch("rfq_workbench.knowledge.date", EffectiveDate),
    ):
        work = Path(tmp)
        for case in suite.cases:
            start = perf_counter()
            identity = case["id"]
            folder = FROZEN / "cases" / identity
            expected = suite.gold["cases"][identity]
            settings = isolated(base, work, identity, folder)
            with TestClient(create_app(settings)) as client:
                run, observation = extract(client, folder, mode)
                fields = score_fields(
                    run,
                    suite.gold["field_order"],
                    expected["fields"],
                    suite.protocol["drawing_page"],
                )
                if observation["analyze"]["status"] != 200:
                    # A failed call did not abstain correctly.
                    for field in fields:
                        field["outcome"] = "execution_failure"
                row = (
                    case
                    | observation
                    | {
                        "scenario": "quote" if "quote" in expected else "block",
                        "fields": fields,
                    }
                )
                if mode == "local_text":
                    row["business"] = quote_checks(client, run, identity, expected, settings)
                    if "cost_through_export_ms" in row["business"]:
                        # Includes upload/extraction + API checks through export, excludes later assertions.
                        row["upload_through_export_ms"] = round(
                            row["upload_through_extraction_ms"]
                            + row["business"]["cost_through_export_ms"],
                            2,
                        )
                    row["knowledge"] = knowledge_checks(
                        client, identity, expected, settings.reference_path
                    )
                    review = review_checks(
                        client, run, identity, expected, FROZEN / "quantity-addendum.txt"
                    )
                    if review:
                        faults[f"review_{identity}"] = review
                row["case_wall_ms"] = round((perf_counter() - start) * 1000, 2)
                rows.append(row)
            write_report(output, report)
            print(
                f"{identity}: {row['analyze']['status']} ({row['upload_through_extraction_ms']} ms)",
                flush=True,
            )

        first = FROZEN / "cases/F01"
        with TestClient(create_app(isolated(base, work, "injection", first))) as client:
            faults["document_prompt_injection"] = injection(
                client, first, mode, suite, suite.gold["cases"]["F01"]
            )
        if mode == "local_text":
            faults["timeout"] = timeout_check(isolated(base, work, "timeout", first), first)
            default = replace(base, data_dir=work / "default-scope")
            with TestClient(create_app(default)) as client:
                run, row = extract(client, first, mode)
                cost = result(mutate(client, run, "cost"))
                faults["demo_scope"] = {
                    "cost": cost,
                    "checks": {
                        "no_template_for_holdout_part": "NO_TEMPLATE"
                        in {i["code"] for i in row["issues"]}
                        and cost == {"status": 422, "code": "REVIEW_REQUIRED"}
                        and no_draft(read(client, run)),
                    },
                }
            expired = isolated(base, work, "expired-payload", FROZEN / "cases/F12")
            expired.reference_path.write_bytes(
                expired.reference_path.read_bytes().replace(
                    b'"valid_until": "2027-12-31"', b'"valid_until": "2026-02-01"'
                )
            )
            with TestClient(create_app(expired)) as client:
                run, row = extract(client, FROZEN / "cases/F12", mode)
                faults["expired_payload"] = {
                    "analyze": row["analyze"],
                    "checks": {
                        "expired_payload_blocks": row["analyze"]
                        == {"status": 422, "code": "INVALID_RATE"}
                        and result(mutate(client, run, "cost"))
                        == {"status": 422, "code": "REVIEW_REQUIRED"}
                        and no_draft(read(client, run)),
                    },
                }
    report["summary"] = summarize(rows, faults)
    report["complete"] = True
    write_report(output, report)
    return report


def acceptance_passes(report: dict) -> bool:
    summary = report["summary"]
    if summary["faults_passed"] != summary["faults_total"]:
        return False
    if report["mode"] == "live":
        return (
            summary["api_success"] == summary["packages"]
            and all(r["metrics"].get("real_model_call") is True for r in report["cases"])
            and report["faults"]["document_prompt_injection"]["metrics"].get("real_model_call")
            is True
        )
    return (
        summary["api_success"] == summary["packages"]
        and summary["quote"]["positive_passed"] == summary["quote"]["positive_total"] == 4
        and summary["quote"]["expected_block_passed"]
        == summary["quote"]["expected_block_total"]
        == 8
        and summary["knowledge_cases_passed"] == summary["knowledge_cases_total"] == 12
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["local_text", "live"], default="local_text")
    parser.add_argument("--output", type=Path, required=True, help="New subdirectory of var/m7")
    args = parser.parse_args()
    suite = Suite.load()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / "var/m7") or out == ROOT / "var/m7" or out.exists():
        parser.error("--output must be a new subdirectory of var/m7")
    base = Settings(data_dir=out, model_pricing_path=PRICING)
    if args.mode == "live":
        configured = Settings.from_env()
        if not configured.model_key or not configured.model_name:
            parser.error("Live run requires .env.local model configuration")
        if configured.simulate_model_timeout:
            parser.error("Disable timeout simulation for live evaluation")
        base = replace(
            base,
            model_key=configured.model_key,
            model_name=configured.model_name,
            model_base_url=configured.model_base_url,
            model_timeout=suite.protocol["live"]["timeout_seconds"],
        )
    out.mkdir(parents=True)
    report = evaluate(suite, out, args.mode, base)
    print(json.dumps(report["summary"], indent=2))
    print(f"Report: {out / 'report.json'}")
    if not acceptance_passes(report):
        raise SystemExit(
            "Evaluation completed with unmet checks; see report (no automatic fallback)."
        )


if __name__ == "__main__":
    main()
