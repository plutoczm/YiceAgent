from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.business_data.models import DeliveryValidationError
from app.business_data.serialization import canonical_json_bytes

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "business_data" / "v1" / "contract.json"
)
DATASET_FILE_NAMES = {
    "orders_v1": "orders_v1.jsonl",
    "tickets_v1": "tickets_v1.jsonl",
    "ticket_events_v1": "ticket_events_v1.jsonl",
    "actions_v1": "actions_v1.jsonl",
}
EXPORT_FORMAT_VERSION = "v2"
SUPPORTED_ENCODING = "utf-8"
SUPPORTED_MEDIA_TYPE = "application/x-ndjson"


def load_contract() -> dict[str, Any]:
    try:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryValidationError("unreadable_business_data_contract") from exc
    if contract.get("contract_name") != "supportops_business_data":
        raise DeliveryValidationError("unexpected_contract_name")
    if contract.get("version") != "v1":
        raise DeliveryValidationError("unexpected_contract_version")
    dataset_names = [dataset.get("name") for dataset in contract.get("datasets", [])]
    if dataset_names != list(DATASET_FILE_NAMES):
        raise DeliveryValidationError("unsupported_contract_datasets")
    return contract


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def content_snapshot_id(
    *,
    contract_sha256: str,
    contract_version: str,
    tenant_id: str,
    currency: str,
    datasets: list[dict[str, object]],
) -> str:
    material = {
        "contract_sha256": contract_sha256,
        "contract_version": contract_version,
        "currency": currency,
        "datasets": [
            {
                "content_sha256": dataset["content_sha256"],
                "dataset_name": dataset["dataset_name"],
                "row_count": dataset["row_count"],
            }
            for dataset in datasets
        ],
        "tenant_id": tenant_id,
    }
    return sha256_hex(canonical_json_bytes(material))
