"""Local configuration. Credentials never cross the API boundary."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    reference_path: Path = ROOT / "fixtures/reference/manufacturing.json"
    engineer_token: str = "demo-engineer"
    approver_token: str = "demo-approver"
    allowed_origin: str = "http://127.0.0.1:3000"
    model_key: str = field(default="", repr=False)
    model_name: str = ""
    model_base_url: str = "https://api.openai.com/v1"
    model_timeout: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env.local")
        path = Path(os.environ.get("RFQ_DATA_DIR", "var"))
        return cls(
            data_dir=path if path.is_absolute() else ROOT / path,
            engineer_token=os.environ.get("RFQ_ENGINEER_TOKEN") or "demo-engineer",
            approver_token=os.environ.get("RFQ_APPROVER_TOKEN") or "demo-approver",
            allowed_origin=os.environ.get("RFQ_ALLOWED_ORIGIN", "http://127.0.0.1:3000"),
            model_key=os.environ.get("OPENAI_API_KEY", ""),
            model_name=os.environ.get("OPENAI_MODEL", ""),
            model_base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            model_timeout=float(os.environ.get("RFQ_MODEL_TIMEOUT_SECONDS", "60")),
        )
