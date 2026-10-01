from __future__ import annotations

import json
from typing import Any

from app.business_data.models import ContractCompatibilityError
from app.business_data.protocol import CONTRACT_PATH, load_contract

BASELINE_PATH = CONTRACT_PATH.with_name("compatibility_baseline.json")


def compatibility_surface(contract: dict[str, Any]) -> dict[str, object]:
    """Return the frozen structural v1 surface without copying prose descriptions."""

    return {
        "contract_name": contract["contract_name"],
        "contract_version": contract["version"],
        "datasets": [
            {
                "name": dataset["name"],
                "grain": dataset["grain"],
                "primary_key": dataset["primary_key"]["fields"],
                "sensitivity": dataset["sensitivity"],
                "fields": [_field_surface(field) for field in dataset["fields"]],
            }
            for dataset in contract["datasets"]
        ],
    }


def assert_contract_v1_compatible(
    current: dict[str, Any],
    baseline: dict[str, Any],
) -> None:
    if current.get("contract_name") != baseline.get("contract_name"):
        raise ContractCompatibilityError("contract_name_changed")
    if current.get("version") != baseline.get("contract_version"):
        raise ContractCompatibilityError("contract_version_changed")

    current_datasets = {dataset["name"]: dataset for dataset in current.get("datasets", [])}
    baseline_datasets = {dataset["name"]: dataset for dataset in baseline.get("datasets", [])}
    if set(current_datasets) != set(baseline_datasets):
        raise ContractCompatibilityError("dataset_set_changed")

    for name, frozen_dataset in baseline_datasets.items():
        current_dataset = current_datasets[name]
        if current_dataset["grain"] != frozen_dataset["grain"]:
            raise ContractCompatibilityError(f"dataset_grain_changed:{name}")
        if current_dataset["primary_key"]["fields"] != frozen_dataset["primary_key"]:
            raise ContractCompatibilityError(f"dataset_primary_key_changed:{name}")
        if current_dataset["sensitivity"] != frozen_dataset["sensitivity"]:
            raise ContractCompatibilityError(f"dataset_sensitivity_changed:{name}")

        current_fields = {field["name"]: field for field in current_dataset["fields"]}
        frozen_fields = {field["name"]: field for field in frozen_dataset["fields"]}
        if not set(frozen_fields).issubset(current_fields):
            raise ContractCompatibilityError(f"field_removed:{name}")

        for field_name, frozen_field in frozen_fields.items():
            current_field = current_fields[field_name]
            if current_field["logical_type"] != frozen_field["logical_type"]:
                raise ContractCompatibilityError(f"field_logical_type_changed:{name}.{field_name}")
            if current_field["nullable"] != frozen_field["nullable"]:
                raise ContractCompatibilityError(f"field_nullable_changed:{name}.{field_name}")
            if current_field["sensitivity"] != frozen_field["sensitivity"]:
                raise ContractCompatibilityError(f"field_sensitivity_changed:{name}.{field_name}")
            if _enum_values(current_field) != frozen_field["enum_values"]:
                raise ContractCompatibilityError(f"field_enum_changed:{name}.{field_name}")

        for field_name, current_field in current_fields.items():
            if field_name not in frozen_fields and not current_field["nullable"]:
                raise ContractCompatibilityError(f"new_required_field:{name}.{field_name}")


def check_current_contract_v1_compatibility() -> None:
    try:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractCompatibilityError("unreadable_compatibility_baseline") from exc
    assert_contract_v1_compatible(load_contract(), baseline)


def _field_surface(field: dict[str, Any]) -> dict[str, object]:
    return {
        "name": field["name"],
        "logical_type": field["logical_type"],
        "nullable": field["nullable"],
        "sensitivity": field["sensitivity"],
        "enum_values": _enum_values(field),
    }


def _enum_values(field: dict[str, Any]) -> list[str] | None:
    values = field.get("enum_values")
    return list(values) if values is not None else None
