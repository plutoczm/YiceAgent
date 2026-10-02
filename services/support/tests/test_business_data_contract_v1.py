import json
from pathlib import Path

CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "contracts" / "business_data" / "v1" / "contract.json"
)
EXPECTED_DATASETS = {"orders_v1", "tickets_v1", "ticket_events_v1", "actions_v1"}
ALLOWED_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
ALLOWED_LOGICAL_TYPES = {"identifier", "string", "enum", "decimal", "timestamp", "boolean"}
PROHIBITED_FIELD_NAMES = {
    "body",
    "context",
    "details",
    "note",
    "payload",
    "prompt",
    "reason",
    "response",
    "result",
    "snapshot",
    "trace_id",
}


def load_contract() -> dict[str, object]:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    contract = json.loads(text)

    # The committed file is a static, canonical JSON document: no database, service,
    # clock, or runtime configuration contributes to the contract definition.
    assert text == json.dumps(contract, indent=2) + "\n"
    return contract


def test_contract_is_parseable_and_declares_v1_boundary() -> None:
    contract = load_contract()

    assert contract["contract_name"] == "supportops_business_data"
    assert contract["version"] == "v1"
    assert contract["owner"] == "SupportOps"
    assert isinstance(contract["description"], str) and contract["description"]
    assert set(contract["data_classifications"]) == ALLOWED_CLASSIFICATIONS
    assert set(contract["compatibility"]) == {"v1_minor_allows", "new_major_required_for"}


def test_contract_contains_exactly_the_v1_datasets_with_unique_names() -> None:
    contract = load_contract()
    datasets = contract["datasets"]

    assert isinstance(datasets, list)
    names = [dataset["name"] for dataset in datasets]
    assert set(names) == EXPECTED_DATASETS
    assert len(names) == len(set(names))


def test_each_dataset_has_a_complete_stable_shape() -> None:
    contract = load_contract()

    for dataset in contract["datasets"]:
        assert set(dataset) >= {
            "name",
            "description",
            "grain",
            "primary_key",
            "relationships",
            "sensitivity",
            "stability",
            "fields",
        }
        assert isinstance(dataset["description"], str) and dataset["description"]
        assert isinstance(dataset["grain"], str) and dataset["grain"]
        assert dataset["sensitivity"] in ALLOWED_CLASSIFICATIONS
        assert dataset["stability"] == {
            "level": "stable",
            "compatibility": "Subject to the contract-level v1.x compatibility rules.",
        }

        primary_key = dataset["primary_key"]
        assert isinstance(primary_key, dict)
        assert isinstance(primary_key["fields"], list) and primary_key["fields"]
        assert isinstance(primary_key["semantic_meaning"], str) and primary_key["semantic_meaning"]


def test_fields_are_unique_complete_and_classified() -> None:
    contract = load_contract()

    for dataset in contract["datasets"]:
        fields = dataset["fields"]
        assert isinstance(fields, list) and fields
        names = [field["name"] for field in fields]
        assert len(names) == len(set(names))
        assert not PROHIBITED_FIELD_NAMES.intersection(names)

        for field in fields:
            assert set(field) >= {
                "name",
                "description",
                "logical_type",
                "nullable",
                "semantic_meaning",
                "sensitivity",
            }
            assert isinstance(field["name"], str) and field["name"]
            assert isinstance(field["description"], str) and field["description"]
            assert field["logical_type"] in ALLOWED_LOGICAL_TYPES
            assert isinstance(field["nullable"], bool)
            assert isinstance(field["semantic_meaning"], str) and field["semantic_meaning"]
            assert field["sensitivity"] in ALLOWED_CLASSIFICATIONS
            if field["logical_type"] == "enum":
                assert isinstance(field.get("enum_values"), list) and field["enum_values"]


def test_primary_keys_and_relationships_reference_declared_contract_fields() -> None:
    contract = load_contract()
    datasets_by_name = {dataset["name"]: dataset for dataset in contract["datasets"]}

    for dataset in datasets_by_name.values():
        field_names = {field["name"] for field in dataset["fields"]}
        assert set(dataset["primary_key"]["fields"]).issubset(field_names)

        for relationship in dataset["relationships"]:
            assert relationship["field"] in field_names
            assert relationship["target_dataset"] in datasets_by_name
            assert (
                relationship["target_primary_key"]
                == datasets_by_name[relationship["target_dataset"]]["primary_key"]["fields"]
            )
            assert relationship["cardinality"] == "many-to-one"
            assert isinstance(relationship["nullable"], bool)
            assert isinstance(relationship["semantic_meaning"], str)
            assert relationship["semantic_meaning"]


def test_decimal_fields_have_business_not_float_semantics() -> None:
    contract = load_contract()
    decimal_fields = {
        (dataset["name"], field["name"]): field
        for dataset in contract["datasets"]
        for field in dataset["fields"]
        if field["logical_type"] == "decimal"
    }

    assert set(decimal_fields) == {
        ("orders_v1", "amount"),
        ("orders_v1", "refundable_amount"),
        ("actions_v1", "quoted_amount"),
    }
    for field in decimal_fields.values():
        assert field["unit"] == "major currency units"
        assert field["serialization"] == "Canonical base-10 decimal string; no exponent notation."
        assert isinstance(field["currency_semantics"], str) and field["currency_semantics"]
