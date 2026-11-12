"""Local configuration. Credentials never cross the API boundary."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[4]
REFERENCE_DIR = ROOT / "fixtures/reference"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    reference_path: Path = REFERENCE_DIR / "manufacturing.json"
    knowledge_path: Path = REFERENCE_DIR / "knowledge.json"
    engineer_token: str = "demo-engineer"
    approver_token: str = "demo-approver"
    allowed_origin: str = "http://127.0.0.1:3000"
    model_key: str = field(default="", repr=False)
    model_name: str = ""
    model_base_url: str = "https://api.openai.com/v1"
    model_timeout: float = 60.0
    simulate_model_timeout: bool = False
    model_pricing_path: Path = ROOT / "config/model-pricing.json"
    sync_max_attempts: int = 5
    sync_lease_seconds: int = 60
    snapshot_ttl_seconds: int = 900
    fake_systems_path: Path | None = None
    sync_fault_plan: str = ""
    agent_enabled: bool = False
    agent_model_api: str = "auto"

    @property
    def systems_path(self) -> Path:
        return self.fake_systems_path or self.data_dir / "fake-systems.sqlite3"

    def __post_init__(self):
        if self.agent_model_api not in {"auto", "responses", "chat"}:
            raise ValueError("RFQ_AGENT_MODEL_API must be auto, responses or chat.")
        for value in (self.sync_max_attempts, self.sync_lease_seconds, self.snapshot_ttl_seconds):
            if type(value) is not int or value <= 0:
                raise ValueError("Integration budgets and TTL must be positive integers.")
        if self.systems_path.resolve() == (self.data_dir / "workbench.sqlite3").resolve():
            raise ValueError("Simulated systems must have an independent database.")
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
        profile = (os.environ.get("RFQ_RATE_PROFILE") or "A").strip().upper()
        if profile not in {"A", "B"}:
            raise ValueError("RFQ_RATE_PROFILE must be A or B.")

        reference_dir = REFERENCE_DIR
        data_dir = ROOT / Path(os.environ.get("RFQ_DATA_DIR", "var"))
        fake_path = os.environ.get("RFQ_FAKE_SYSTEMS_PATH")
        fake_systems_path = Path(fake_path) if fake_path else None
        if profile == "B":
            # Simulated ERP references are seeded once, so each profile needs its own store.
            reference_dir /= "rate-variant"
            data_dir /= "rate-variant"
            if fake_systems_path is not None:
                fake_systems_path = fake_systems_path.with_stem(
                    f"{fake_systems_path.stem}.rate-variant"
                )

        return cls(
            data_dir=data_dir,
            reference_path=reference_dir / "manufacturing.json",
            knowledge_path=reference_dir / "knowledge.json",
            engineer_token=os.environ.get("RFQ_ENGINEER_TOKEN") or "demo-engineer",
            approver_token=os.environ.get("RFQ_APPROVER_TOKEN") or "demo-approver",
            allowed_origin=os.environ.get("RFQ_ALLOWED_ORIGIN", "http://127.0.0.1:3000"),
            model_key=os.environ.get("OPENAI_API_KEY", ""),
            model_name=os.environ.get("OPENAI_MODEL", ""),
            model_base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            model_timeout=float(os.environ.get("RFQ_MODEL_TIMEOUT_SECONDS", "60")),
            simulate_model_timeout=os.environ.get("RFQ_SIMULATE_MODEL_TIMEOUT") == "1",
            sync_max_attempts=int(os.environ.get("RFQ_SYNC_MAX_ATTEMPTS", "5")),
            sync_lease_seconds=int(os.environ.get("RFQ_SYNC_LEASE_SECONDS", "60")),
            snapshot_ttl_seconds=int(os.environ.get("RFQ_SNAPSHOT_TTL_SECONDS", "900")),
            fake_systems_path=fake_systems_path,
            sync_fault_plan=os.environ.get("RFQ_SYNC_FAULT_PLAN", ""),
            agent_enabled=os.environ.get("RFQ_AGENT_ENABLED") == "1",
            agent_model_api=os.environ.get("RFQ_AGENT_MODEL_API", "auto"),
            model_pricing_path=Path(
                os.environ.get("RFQ_MODEL_PRICING_FILE", str(ROOT / "config/model-pricing.json"))
            ),
        )
