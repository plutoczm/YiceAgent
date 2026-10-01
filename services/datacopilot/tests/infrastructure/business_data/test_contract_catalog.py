import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsContextError,
    BusinessCatalogMismatchError,
    BusinessScopeViolationError,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
    BusinessAnalyticsStatus,
    BusinessFieldClassification,
    BusinessLogicalType,
)
from backend.app.application.business_analytics.workflow import (
    BusinessAnalyticsWorkflow,
)
from backend.app.infrastructure.business_data.contract_catalog import (
    ACCEPTANCE_RELATIVE_PATH,
    CONTRACT_RELATIVE_PATH,
    MAX_CONTRACT_BYTES,
    BusinessDataContractCatalog,
)
from backend.app.infrastructure.business_data.errors import (
    BusinessContractFormatError,
    BusinessContractIdentityError,
    BusinessContractIntegrityError,
    BusinessContractLoadError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = PROJECT_ROOT / CONTRACT_RELATIVE_PATH
ACCEPTANCE_PATH = PROJECT_ROOT / ACCEPTANCE_RELATIVE_PATH
EXPECTED_CONTRACT_SHA256 = (
    "7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b"
)


def make_context(
    *,
    tenant_id: str = "tenant-a",
    request_id: str = "request-1",
    contract_name: str = "supportops_business_data",
    contract_version: str = "v1",
    allowed_datasets: tuple[str, ...] = (
        "orders_v1",
        "tickets_v1",
        "ticket_events_v1",
        "actions_v1",
    ),
) -> BusinessAnalyticsContext:
    return BusinessAnalyticsContext(
        request_id=request_id,
        tenant_id=tenant_id,
        contract_name=contract_name,
        contract_version=contract_version,
        allowed_datasets=allowed_datasets,
    )


def load_contract_data() -> dict[str, object]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def serialize_contract(data: dict[str, object]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_catalog(
    tmp_path: Path,
    contract_bytes: bytes,
    *,
    acceptance_updates: dict[str, object] | None = None,
    acceptance_bytes: bytes | None = None,
) -> BusinessDataContractCatalog:
    contract_path = tmp_path / "contract.json"
    acceptance_path = tmp_path / "acceptance.json"
    contract_path.write_bytes(contract_bytes)
    if acceptance_bytes is None:
        acceptance = json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8"))
        acceptance["contract_sha256"] = hashlib.sha256(contract_bytes).hexdigest()
        if acceptance_updates:
            acceptance.update(acceptance_updates)
        acceptance_bytes = (
            json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
    acceptance_path.write_bytes(acceptance_bytes)
    return BusinessDataContractCatalog(
        contract_path=contract_path,
        acceptance_path=acceptance_path,
    )


def make_mutated_contract(
    mutation: Callable[[dict[str, object]], None],
) -> bytes:
    data = load_contract_data()
    mutation(data)
    return serialize_contract(data)


def test_accepted_business_contract_v1_loads_and_maps_typed_semantics() -> None:
    contract_bytes = CONTRACT_PATH.read_bytes()
    assert hashlib.sha256(contract_bytes).hexdigest() == EXPECTED_CONTRACT_SHA256
    acceptance = json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8"))
    assert acceptance["contract_sha256"] == EXPECTED_CONTRACT_SHA256

    snapshot = BusinessDataContractCatalog.from_repository_root(PROJECT_ROOT).resolve(
        make_context()
    )

    assert snapshot.tenant_id == "tenant-a"
    assert snapshot.contract_name == "supportops_business_data"
    assert snapshot.contract_version == "v1"
    assert [
        definition.classification for definition in snapshot.classification_definitions
    ] == list(BusinessFieldClassification)
    assert "tenant-scoped access" in next(
        definition.meaning
        for definition in snapshot.classification_definitions
        if definition.classification is BusinessFieldClassification.CONFIDENTIAL
    )
    assert [dataset.logical_name for dataset in snapshot.datasets] == [
        "orders_v1",
        "tickets_v1",
        "ticket_events_v1",
        "actions_v1",
    ]
    expected_primary_keys = {
        "orders_v1": ("tenant_id", "order_id"),
        "tickets_v1": ("tenant_id", "ticket_id"),
        "ticket_events_v1": ("tenant_id", "event_id"),
        "actions_v1": ("tenant_id", "action_id"),
    }
    assert {
        dataset.logical_name: dataset.primary_key.fields
        for dataset in snapshot.datasets
        if dataset.primary_key is not None
    } == expected_primary_keys
    assert [len(dataset.fields) for dataset in snapshot.datasets] == [8, 10, 7, 14]

    orders = snapshot.datasets[0]
    assert orders.grain == (
        "One row represents one order in one tenant at the time the dataset "
        "is produced."
    )
    assert orders.primary_key is not None
    assert orders.primary_key.fields == ("tenant_id", "order_id")
    assert orders.classification is BusinessFieldClassification.CONFIDENTIAL
    assert orders.stability is not None and orders.stability.level.value == "stable"

    order_fields = {field.name: field for field in orders.fields}
    assert order_fields["amount"].logical_type is BusinessLogicalType.DECIMAL
    assert order_fields["amount"].description == (
        "Original order monetary amount used by SupportOps."
    )
    assert order_fields["amount"].meaning.startswith(
        "A monetary amount in major currency units"
    )
    assert order_fields["amount"].unit == "major currency units"
    assert order_fields["amount"].serialization == (
        "Canonical base-10 decimal string; no exponent notation."
    )
    assert order_fields["amount"].currency_semantics is not None
    assert order_fields["order_id"].classification is (
        BusinessFieldClassification.CONFIDENTIAL
    )

    tickets = snapshot.datasets[1]
    ticket_fields = {field.name: field for field in tickets.fields}
    assert ticket_fields["status"].logical_type is BusinessLogicalType.ENUM
    assert ticket_fields["status"].enum_values == (
        "open",
        "assigned",
        "pending_customer",
        "resolved",
        "closed",
    )
    assert ticket_fields["sla_due_at"].logical_type is BusinessLogicalType.TIMESTAMP
    assert ticket_fields["sla_due_at"].timestamp_semantics is not None

    ticket_events = snapshot.datasets[2]
    assert len(ticket_events.relationships) == 1
    event_relation = ticket_events.relationships[0]
    assert event_relation.name == "ticket_id"
    assert event_relation.source_fields == ("tenant_id", "ticket_id")
    assert event_relation.target_dataset == "tickets_v1"
    assert event_relation.target_fields == ("tenant_id", "ticket_id")
    assert event_relation.meaning is not None

    actions = snapshot.datasets[3]
    assert len(actions.relationships) == 2
    action_fields = {field.name: field for field in actions.fields}
    assert action_fields["order_id"].references is not None
    assert action_fields["order_id"].references.target_dataset == "orders_v1"
    assert action_fields["quoted_amount"].logical_type is BusinessLogicalType.DECIMAL
    assert action_fields["action_status"].enum_values == (
        "pending_confirmation",
        "pending_approval",
        "submitted",
        "cancelled",
        "expired",
        "rejected",
        "invalidated",
    )
    assert snapshot.catalog_fingerprint == hashlib.sha256(
        (
            EXPECTED_CONTRACT_SHA256
            + "\n"
            + "\n".join(
                (
                    "actions_v1",
                    "orders_v1",
                    "ticket_events_v1",
                    "tickets_v1",
                )
            )
        ).encode("utf-8")
    ).hexdigest()


def test_allowed_scope_filters_datasets_relationships_and_field_references() -> None:
    snapshot = BusinessDataContractCatalog.from_repository_root(PROJECT_ROOT).resolve(
        make_context(allowed_datasets=("actions_v1",))
    )

    assert [dataset.logical_name for dataset in snapshot.datasets] == ["actions_v1"]
    actions = snapshot.datasets[0]
    assert actions.relationships == ()
    assert all(field.references is None for field in actions.fields)
    assert actions.primary_key is not None
    assert actions.primary_key.fields == ("tenant_id", "action_id")


def test_unknown_allowed_dataset_fails_closed() -> None:
    catalog = BusinessDataContractCatalog.from_repository_root(PROJECT_ROOT)

    with pytest.raises(BusinessScopeViolationError):
        catalog.resolve(make_context(allowed_datasets=("orders_v1", "missing_v1")))


def test_catalog_fingerprint_uses_contract_hash_and_sorted_scope_only(
    tmp_path: Path,
) -> None:
    catalog = BusinessDataContractCatalog.from_repository_root(PROJECT_ROOT)
    first = catalog.resolve(
        make_context(
            tenant_id="tenant-a",
            request_id="request-one",
            allowed_datasets=("tickets_v1", "orders_v1"),
        )
    )
    second = catalog.resolve(
        make_context(
            tenant_id="tenant-b",
            request_id="request-two",
            allowed_datasets=("orders_v1", "tickets_v1"),
        )
    )
    narrower = catalog.resolve(
        make_context(allowed_datasets=("orders_v1",))
    )
    relocated = write_catalog(tmp_path, CONTRACT_PATH.read_bytes()).resolve(
        make_context(
            tenant_id="tenant-z",
            request_id="request-three",
            allowed_datasets=("orders_v1", "tickets_v1"),
        )
    )

    assert first.tenant_id != second.tenant_id
    assert first.catalog_fingerprint == second.catalog_fingerprint
    assert first.catalog_fingerprint != narrower.catalog_fingerprint
    assert first.catalog_fingerprint == relocated.catalog_fingerprint


def test_real_catalog_adapter_integrates_with_d1_workflow() -> None:
    catalog = BusinessDataContractCatalog.from_repository_root(PROJECT_ROOT)
    workflow = BusinessAnalyticsWorkflow(catalog_port=catalog)
    context = make_context(
        allowed_datasets=("tickets_v1", "ticket_events_v1"),
    )

    result = workflow.prepare(
        request=BusinessAnalyticsRequest(
            question="Count ticket transitions",
            requested_datasets=("ticket_events_v1",),
        ),
        context=context,
    )

    assert result.request_id == context.request_id
    assert result.resolved_datasets == ("ticket_events_v1",)
    assert result.status is BusinessAnalyticsStatus.PREPARED
    assert result.catalog_fingerprint == catalog.resolve(context).catalog_fingerprint


def test_real_catalog_adapter_and_d1_workflow_reject_untrusted_scope() -> None:
    workflow = BusinessAnalyticsWorkflow(
        catalog_port=BusinessDataContractCatalog.from_repository_root(PROJECT_ROOT)
    )

    with pytest.raises(BusinessScopeViolationError):
        workflow.prepare(
            request=BusinessAnalyticsRequest(
                question="Read actions",
                requested_datasets=("actions_v1",),
            ),
            context=make_context(allowed_datasets=("orders_v1",)),
        )


def test_context_contract_identity_must_match_the_acceptance_pin() -> None:
    catalog = BusinessDataContractCatalog.from_repository_root(PROJECT_ROOT)

    with pytest.raises(BusinessCatalogMismatchError):
        catalog.resolve(make_context(contract_version="v2"))

    with pytest.raises(BusinessAnalyticsContextError):
        catalog.resolve(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.__setitem__("unexpected", True),
        lambda data: data.pop("compatibility"),
        lambda data: data["datasets"].__setitem__(1, data["datasets"][0]),
        lambda data: data["datasets"][0].pop("name"),
        lambda data: data["datasets"][0]["fields"].__setitem__(
            1, data["datasets"][0]["fields"][0]
        ),
        lambda data: data["datasets"][0]["primary_key"].__setitem__(
            "fields", ["unknown"]
        ),
        lambda data: data["datasets"][2]["relationships"][0].__setitem__(
            "target_dataset", "missing_v1"
        ),
        lambda data: data["datasets"][2]["relationships"][0].__setitem__(
            "field", "missing"
        ),
        lambda data: data["datasets"][2]["relationships"][0].__setitem__(
            "target_primary_key", ["tenant_id", "missing"]
        ),
        lambda data: data["datasets"][0]["fields"][0].__setitem__(
            "sensitivity", "secret"
        ),
        lambda data: data["datasets"][1]["fields"][4].__setitem__(
            "enum_values", ["open", "open"]
        ),
        lambda data: data["datasets"][1]["fields"][4].__setitem__("enum_values", None),
        lambda data: data["datasets"][1]["fields"][4].__setitem__(
            "logical_type", "unknown-type"
        ),
        lambda data: data["datasets"][3]["fields"][2].__setitem__(
            "references", "orders_v1.(tenant_id,missing)"
        ),
        lambda data: data["datasets"][3]["fields"][2].__setitem__(
            "nullable", "true"
        ),
    ],
    ids=(
        "unknown-top-level-field",
        "missing-contract-metadata",
        "duplicate-dataset",
        "missing-dataset-name",
        "duplicate-field",
        "invalid-primary-key-reference",
        "missing-relationship-dataset",
        "missing-relationship-source-field",
        "invalid-relationship-key",
        "unknown-classification",
        "duplicate-enum-value",
        "missing-enum-values",
        "unknown-logical-type",
        "invalid-field-reference",
        "coerced-nullable",
    ),
)
def test_malformed_contract_shapes_fail_closed(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    contract_bytes = make_mutated_contract(mutation)
    catalog = write_catalog(tmp_path, contract_bytes)

    with pytest.raises(BusinessContractFormatError):
        catalog.resolve(make_context())


@pytest.mark.parametrize(
    "contract_bytes",
    [
        b"not-json",
        b"\xef\xbb\xbf{}",
        (
            b'{"contract_name":"first","contract_name":"second",'
            b'"version":"v1"}'
        ),
        b'{"value":NaN}',
    ],
    ids=("invalid-json", "utf8-bom", "duplicate-json-key", "nonstandard-constant"),
)
def test_json_parser_rejects_invalid_encoding_syntax_and_duplicate_keys(
    tmp_path: Path,
    contract_bytes: bytes,
) -> None:
    catalog = write_catalog(tmp_path, contract_bytes)

    with pytest.raises(BusinessContractFormatError):
        catalog.resolve(make_context())


def test_acceptance_metadata_is_also_strict_and_rejects_duplicate_keys(
    tmp_path: Path,
) -> None:
    acceptance_bytes = (
        b'{"contract_name":"supportops_business_data",'
        b'"contract_name":"other","accepted_version":"v1"}'
    )
    catalog = write_catalog(
        tmp_path,
        CONTRACT_PATH.read_bytes(),
        acceptance_bytes=acceptance_bytes,
    )

    with pytest.raises(BusinessContractFormatError):
        catalog.resolve(make_context())


def test_contract_sha256_mismatch_fails_closed_without_exposing_path(
    tmp_path: Path,
) -> None:
    catalog = write_catalog(
        tmp_path,
        CONTRACT_PATH.read_bytes(),
        acceptance_updates={"contract_sha256": "0" * 64},
    )

    with pytest.raises(BusinessContractIntegrityError) as error:
        catalog.resolve(make_context())

    assert str(tmp_path) not in str(error.value)
    assert EXPECTED_CONTRACT_SHA256 not in str(error.value)


def test_contract_identity_must_match_acceptance_metadata(tmp_path: Path) -> None:
    contract_bytes = make_mutated_contract(
        lambda data: data.__setitem__("contract_name", "other_contract")
    )
    catalog = write_catalog(tmp_path, contract_bytes)

    with pytest.raises(BusinessContractIdentityError):
        catalog.resolve(make_context())


def test_contract_version_must_match_the_pinned_v1_format(tmp_path: Path) -> None:
    contract_bytes = make_mutated_contract(
        lambda data: data.__setitem__("version", "v2")
    )
    catalog = write_catalog(tmp_path, contract_bytes)

    with pytest.raises(BusinessContractFormatError):
        catalog.resolve(make_context())


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        ("public", BusinessFieldClassification.PUBLIC),
        ("restricted", BusinessFieldClassification.RESTRICTED),
    ],
)
def test_all_contract_classifications_map_without_policy_downgrade(
    tmp_path: Path,
    classification: str,
    expected: BusinessFieldClassification,
) -> None:
    contract_bytes = make_mutated_contract(
        lambda data: data["datasets"][0]["fields"][0].__setitem__(
            "sensitivity", classification
        )
    )
    catalog = write_catalog(tmp_path, contract_bytes)

    snapshot = catalog.resolve(make_context(allowed_datasets=("orders_v1",)))

    assert snapshot.datasets[0].fields[0].classification is expected


def test_acceptance_model_rejects_extra_metadata(tmp_path: Path) -> None:
    acceptance = json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8"))
    acceptance["accepted_at"] = "not-needed"
    catalog = write_catalog(
        tmp_path,
        CONTRACT_PATH.read_bytes(),
        acceptance_bytes=(json.dumps(acceptance) + "\n").encode("utf-8"),
    )

    with pytest.raises(BusinessContractFormatError):
        catalog.resolve(make_context())


def test_contract_file_must_be_regular_and_within_the_size_limit(
    tmp_path: Path,
) -> None:
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_bytes(ACCEPTANCE_PATH.read_bytes())
    directory_catalog = BusinessDataContractCatalog(
        contract_path=tmp_path,
        acceptance_path=acceptance_path,
    )
    with pytest.raises(BusinessContractLoadError):
        directory_catalog.resolve(make_context())

    large_path = tmp_path / "oversized.json"
    large_path.write_bytes(b" " * (MAX_CONTRACT_BYTES + 1))
    large_catalog = BusinessDataContractCatalog(
        contract_path=large_path,
        acceptance_path=acceptance_path,
    )
    with pytest.raises(BusinessContractLoadError):
        large_catalog.resolve(make_context())


def test_contract_symlink_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link_path = tmp_path / "contract-link.json"
    try:
        os.symlink(CONTRACT_PATH, link_path)
    except (NotImplementedError, OSError):
        link_path.write_bytes(CONTRACT_PATH.read_bytes())
        is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == link_path or is_symlink(path),
        )

    catalog = BusinessDataContractCatalog(
        contract_path=link_path,
        acceptance_path=ACCEPTANCE_PATH,
    )
    with pytest.raises(BusinessContractLoadError):
        catalog.resolve(make_context())
