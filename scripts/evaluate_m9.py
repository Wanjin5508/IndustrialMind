"""Evaluate frozen simulated integration effects, uncertainty and mismatch detection."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from m9_scenarios import ROOT, scenario

FROZEN = ROOT / "evals/frozen/m9"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol = json.loads((FROZEN / "protocol.json").read_text("utf-8"))
    for name, expected in protocol["sha256"].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or digest(path) != expected:
            raise SystemExit("M9 frozen input hash changed")
    scenarios = json.loads((FROZEN / "scenarios.json").read_text("utf-8"))
    if {row["id"] for row in scenarios} != {f"M9-{number:02d}" for number in range(1, 18)}:
        raise SystemExit("M9 frozen scenario inventory changed")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "var/m9") or output.exists():
        parser.error("--output must be a new subdirectory of var/m9")
    output.mkdir(parents=True)
    revision = subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    report = {
        "suite": "m9-system-harness-v1",
        "mode": "SIMULATED / NO EXTERNAL WRITE",
        "complete": False,
        "protocol": protocol,
        "git_commit": revision,
        "cases": [],
        "faults": {},
        "summary": {},
    }
    path = output / "report.json"
    write(path, report)
    for expected in scenarios:
        with TemporaryDirectory(dir=output) as work:
            try:
                result = scenario(expected["id"], Path(work))
                passed = (
                    result["state"] == expected["state"]
                    and result["draft_count"] == expected["draft_count"]
                    and result.get("verified_payload_matches", True)
                )
                observed = {
                    "id": expected["id"],
                    "name": expected["name"],
                    "passed": passed,
                    "expected": expected,
                    "observed": result,
                }
            except Exception as exc:
                observed = {
                    "id": expected["id"],
                    "name": expected["name"],
                    "passed": False,
                    "expected": expected,
                    "error": type(exc).__name__,
                }
        report["cases"].append(observed)
        write(path, report)
        print(f"{expected['id']}: {'PASS' if observed['passed'] else 'FAIL'}", flush=True)
    cases = report["cases"]
    summary = {"passed": sum(row["passed"] for row in cases), "total": len(cases)}
    summary["duplicate_effect"] = sum(
        max(
            0,
            row.get("observed", {}).get("draft_count", 0)
            - 1
            - row["expected"].get("injected_extra_records", 0),
        )
        for row in cases
    )
    summary["lost_write"] = sum(
        row["expected"]["state"] == "VERIFIED" and row.get("observed", {}).get("draft_count") != 1
        for row in cases
    )
    summary["false_ack"] = sum(
        row.get("observed", {}).get("state") == "VERIFIED"
        and not row.get("observed", {}).get("verified_payload_matches", False)
        for row in cases
    )
    unsafe = {"M9-10", "M9-11", "M9-13"}
    summary["unsafe_send"] = sum(
        row.get("observed", {}).get("draft_count", 0) for row in cases if row["id"] in unsafe
    )
    unknown = {"M9-02", "M9-03", "M9-05", "M9-07", "M9-09"}
    mismatch = {"M9-08", "M9-12", "M9-14", "M9-17"}
    summary["unknown_outcome_resolved"] = {
        "numerator": sum(row["passed"] for row in cases if row["id"] in unknown),
        "denominator": len(unknown),
    }
    summary["injected_mismatch_detection_recall"] = {
        "numerator": sum(row["passed"] for row in cases if row["id"] in mismatch),
        "denominator": len(mismatch),
    }
    summary["injected_duplicate_records"] = 1
    report["complete"], report["summary"] = True, summary
    write(path, report)
    print(json.dumps(summary, indent=2), flush=True)
    if summary["passed"] != summary["total"] or any(
        summary[key] != 0 for key in ("duplicate_effect", "lost_write", "false_ack", "unsafe_send")
    ):
        raise SystemExit("M9 acceptance gates failed; see report.json")


if __name__ == "__main__":
    main()
