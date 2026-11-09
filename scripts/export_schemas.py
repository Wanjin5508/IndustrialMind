"""Export domain JSON Schemas; run after modifying public contracts."""

import json
from pathlib import Path

from rfq_workbench import contracts

root = Path(__file__).resolve().parents[1] / "docs/schemas"
root.mkdir(parents=True, exist_ok=True)
for name in [
    "ConfirmedInputs",
    "Source",
    "Extraction",
    "Issue",
    "Quote",
    "AuditEvent",
    "EvidenceCheck",
    "ReviewedField",
]:
    schema = getattr(contracts, name).model_json_schema()
    (root / f"{name}.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
print("Exported 8 domain schemas.")
