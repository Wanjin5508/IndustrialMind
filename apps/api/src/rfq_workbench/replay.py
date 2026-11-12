"""Replay a recorded synthetic extraction only against the original file bytes."""

import json

from rfq_workbench.config import ROOT
from rfq_workbench.contracts import Extraction
from rfq_workbench.errors import DomainError
from rfq_workbench.extraction import ExtractionResult

CAPTURE_PATH = ROOT / "fixtures/replay/DEV-001.json"


class ReplayProvider:
    def capture(self, body: dict) -> dict:
        try:
            capture = json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DomainError("REPLAY_UNAVAILABLE", "回放记录无法读取。") from exc
        actual = {d["kind"]: d["sha256"] for d in body["documents"]}
        if (
            not isinstance(capture, dict)
            or capture.get("schema_version") != 1
            or actual != capture.get("documents")
        ):
            raise DomainError("REPLAY_UNAVAILABLE", "没有与当前资料匹配的回放记录。")
        return capture

    def extract(self, body: dict) -> ExtractionResult:
        capture = self.capture(body)
        documents = {d["kind"]: d["id"] for d in body["documents"]}
        fields = {
            kind: {
                name: {
                    "value": fact["value"],
                    "source_id": f"{documents[kind]}:{fact['locator']}"
                    if fact["locator"]
                    else None,
                }
                for name, fact in values.items()
            }
            for kind, values in capture["fields"].items()
        }
        return ExtractionResult(
            Extraction.model_validate(fields),
            {"provider": "REPLAY", "real_model_call": False, "replay_source": capture["source"]},
        )
