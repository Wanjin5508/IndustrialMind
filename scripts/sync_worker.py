"""Perform one explicit simulated ERP dispatch/reconciliation sweep."""

import argparse
import json

from rfq_workbench.config import Settings
from rfq_workbench.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--limit", type=int, default=20, choices=range(1, 101))
    args = parser.parse_args()
    app = create_app(Settings.from_env())
    result = app.state.sync.reconcile_sweep(args.limit)
    print(
        json.dumps(
            {"mode": "SIMULATED", "notice": "NO EXTERNAL WRITE", "intents": result},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
