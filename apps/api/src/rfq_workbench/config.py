"""Local configuration. Credentials never cross the API boundary."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    reference_path: Path = ROOT / "fixtures/reference/manufacturing.json"
    knowledge_path: Path = ROOT / "fixtures/reference/knowledge.json"
    engineer_token: str = "demo-engineer"
    approver_token: str = "demo-approver"
    allowed_origin: str = "http://127.0.0.1:3000"
    model_key: str = field(default="", repr=False)
    model_name: str = ""
    model_base_url: str = "https://api.openai.com/v1"
    model_timeout: float = 60.0
    simulate_model_timeout: bool = False
    model_pricing_path: Path = ROOT / "config/model-pricing.json"

    def __post_init__(self):
        url = urlsplit(self.model_base_url)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Model base URL must be a service URL without embedded credentials or query."
            )
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote model service requires HTTPS.")
        if not math.isfinite(self.model_timeout) or not 1 <= self.model_timeout <= 300:
            raise ValueError("Model timeout must be between 1 and 300 seconds.")
        if (
            not self.engineer_token
            or not self.approver_token
            or self.engineer_token == self.approver_token
        ):
            raise ValueError("Demo role tokens must be distinct and nonempty.")

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
            simulate_model_timeout=os.environ.get("RFQ_SIMULATE_MODEL_TIMEOUT") == "1",
            model_pricing_path=Path(
                os.environ.get("RFQ_MODEL_PRICING_FILE", str(ROOT / "config/model-pricing.json"))
            ),
        )
