"""Opt-in M3 development acceptance: three small synthetic multimodal calls.

This is a regression probe, not a frozen accuracy benchmark. Credentials and
provider error bodies are never written into the report or printed.
"""

import json

from fastapi.testclient import TestClient

from rfq_workbench.config import Settings
from rfq_workbench.main import create_app


def main() -> None:
    settings = Settings.from_env()
    if not settings.model_key or not settings.model_name:
        raise SystemExit("Model configuration is required.")
    report = []
    path = settings.data_dir / "acceptance/m3-live.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(settings)) as client:
        headers = {"X-Demo-Token": settings.engineer_token}
        for case, expected in [
            ("DEV-001", None),
            ("DEV-003", "REVISION_CONFLICT"),
            ("DEV-004", "MISSING_FIELD"),
        ]:
            created = client.post(f"/runs/from-fixture/{case}", headers=headers)
            created.raise_for_status()
            run = created.json()
            response = client.post(
                f"/runs/{run['id']}/analyze",
                headers=headers,
                json={"expected_version": run["version"], "mode": "live"},
            )
            run = client.get(f"/runs/{run['id']}", headers=headers).json()
            codes = {i["code"] for i in run["issues"]}
            passed = response.is_success and run["metrics"].get("real_model_call") is True
            passed = passed and (
                (not codes and run["state"] == "VALIDATING")
                if expected is None
                else expected in codes
            )
            if case == "DEV-004":
                passed = passed and run["fields"].get("drawing.material", {}).get("value") is None
            if expected:
                blocked = client.post(
                    f"/runs/{run['id']}/cost",
                    headers=headers,
                    json={"expected_version": run["version"]},
                )
                passed = passed and blocked.status_code == 422
            report.append(
                {
                    "case": case,
                    "run_id": run["id"],
                    "passed": passed,
                    "state": run["state"],
                    "issues": run["issues"],
                    "metrics": run["metrics"],
                    "fields": run["fields"],
                }
            )
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "case": case,
                        "passed": passed,
                        "issues": sorted(codes),
                        "elapsed_ms": run["metrics"].get("elapsed_ms"),
                        "estimated_cost": run["metrics"].get("estimated_cost"),
                    }
                ),
                flush=True,
            )
    if not all(item["passed"] for item in report):
        raise SystemExit(
            "M3 live probe requires review; inspect local report, no fallback was used."
        )


if __name__ == "__main__":
    main()
