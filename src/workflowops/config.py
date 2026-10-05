"""Trusted local configuration; fixed v2 resources and credential-safe reports."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr

from workflowops.contracts import Closed

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime" / "v2"
OWNER: Literal["workflowops-v2"] = "workflowops-v2"
CONFIG = RUNTIME / "settings.json"


class Settings(Closed):
    owner: Literal["workflowops-v2"] = OWNER
    database_password: SecretStr = Field(min_length=32, repr=False)
    qdrant_key: SecretStr = Field(min_length=32, repr=False)
    deepseek_key: SecretStr | None = Field(default=None, repr=False)
    deepseek_model: Literal["deepseek-flash"] = "deepseek-flash"
    chatgpt_model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:/-]{1,128}$")
    ollama_model: Literal["qwen3:8b"] = "qwen3:8b"
    input_price_cny_per_million: str | None = Field(default=None, pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    output_price_cny_per_million: str | None = Field(default=None, pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    billing_cap_cny: Literal["15.00"] = "15.00"
    paid_calls_enabled: bool = False
    pricing_checked_on: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    pricing_source: Literal["https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"] | None = (
        None
    )
    app_window: Literal[4096, 8192, 16384] = 8192
    inference_device: Literal["auto", "cpu", "cuda:0"] = "auto"

    @classmethod
    def load(cls, path: Path = CONFIG) -> "Settings":
        if path.resolve() != CONFIG.resolve():
            raise ValueError("configuration_path_not_owned")
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def safe_report(self) -> dict[str, object]:
        return {
            "owner": self.owner,
            "deepseek_configured": bool(self.deepseek_key),
            "chatgpt_model_configured": self.chatgpt_model is not None,
            "ollama_model": self.ollama_model,
            "app_window": self.app_window,
            "inference_device": self.inference_device,
            "billing_configured": bool(
                self.input_price_cny_per_million and self.output_price_cny_per_million
            ),
            "billing_cap_cny": self.billing_cap_cny,
            "paid_calls_enabled": self.paid_calls_enabled,
        }


def initialize_settings() -> bool:
    """Create only new owned configuration; never replace existing secrets."""
    import secrets

    if CONFIG.exists():
        Settings.load()
        return False
    RUNTIME.mkdir(parents=True, exist_ok=True)
    values = {
        "owner": OWNER,
        "database_password": secrets.token_urlsafe(36),
        "qdrant_key": secrets.token_urlsafe(36),
    }
    with CONFIG.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(values, indent=2) + "\n")
    return True
