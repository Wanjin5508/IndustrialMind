"""Explicit opt-in live acceptance; sends only DEV-001 synthetic files to the configured model.

Run after configuring .env.local. No fallback provider is used. Result is local,
ignored by Git, and records response identity/usage without saving credentials.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from rfq_workbench.config import Settings
from rfq_workbench.main import create_app


def main() -> None:
    settings = Settings.from_env()
    if not settings.model_key or not settings.model_name:
        raise SystemExit("BLOCKED: configure OPENAI_API_KEY and OPENAI_MODEL in .env.local.")
    headers = {"X-Demo-Token": settings.engineer_token}
    with TestClient(create_app(settings)) as client:
        created = client.post("/runs/from-fixture/DEV-001", headers=headers)
        created.raise_for_status()
        run = created.json()
        result = client.post(
            f"/runs/{run['id']}/analyze",
            headers=headers,
            json={"expected_version": run["version"], "mode": "live"},
        )
        result.raise_for_status()
        run = result.json()
        output = Path("var/acceptance/live-result.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "run_id": run["id"],
                    "state": run["state"],
                    "metrics": run["metrics"],
                    "issues": run["issues"],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert run["metrics"]["real_model_call"] is True
        print(f"Real response recorded: {output}. Review fields in the workbench before approval.")
        if run["issues"]:
            raise SystemExit(
                "REVIEW REQUIRED: live call succeeded, but extraction has blocking issues."
            )


if __name__ == "__main__":
    main()
