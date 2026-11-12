"""Export domain JSON Schemas; run after modifying public contracts."""

import json
from pathlib import Path

from rfq_workbench import contracts
from rfq_workbench.integrations import contracts as integration_contracts
from rfq_workbench.integrations.mappers import ErpDraftPayload

root = Path(__file__).resolve().parents[1] / "docs/04-项目介绍与展示/09-接口模式"
root.mkdir(parents=True, exist_ok=True)
for name in [
    "ConfirmedInputs",
    "Source",
    "Extraction",
    "Issue",
    "Quote",
    "MaterialLine",
    "AuditEvent",
    "EvidenceCheck",
    "ReviewedField",
    "KnowledgeRecord",
    "KnowledgeQuery",
    "TaskRequest",
    "ResumeTask",
]:
    schema = getattr(contracts, name).model_json_schema()
    (root / f"{name}.json").write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
(root / "ErpDraftPayload.json").write_text(
    json.dumps(ErpDraftPayload.model_json_schema(), indent=2) + "\n",
    encoding="utf-8",
    newline="\n",
)
print("Exported 13 domain schemas.")
for name in (
    "ExternalRef",
    "SourceSnapshot",
    "Receipt",
    "FieldDiff",
    "ReconciliationResult",
    "OutboundIntent",
    "SyncRequest",
    "SyncIntentView",
    "ResolveSync",
    "DispatchDue",
):
    schema = getattr(integration_contracts, name).model_json_schema()
    (root / f"{name}.json").write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
