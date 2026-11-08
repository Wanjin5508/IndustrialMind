"""Generate OpenAPI from the application without touching the demo database."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from rfq_workbench.config import Settings
from rfq_workbench.main import create_app

root = Path(__file__).resolve().parents[1]
with TemporaryDirectory() as directory:
    app = create_app(Settings(data_dir=Path(directory)))
    (root / "docs/schemas/openapi.json").write_text(
        json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
print("Generated OpenAPI from application routes.")
