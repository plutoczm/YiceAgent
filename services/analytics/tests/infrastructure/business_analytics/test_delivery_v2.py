from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from backend.app.application.business_analytics.errors import (
    BusinessDataIntegrityError,
    BusinessDataTenantMismatchError,
)
from backend.app.application.business_analytics.governed_models import (
    GovernedExecutionPolicy,
)
from backend.app.application.business_analytics.managed_models import (
    ManagedAnalyticsPolicy,
)
from backend.app.application.business_analytics.managed_policy import (
    build_effective_catalog,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
)
from backend.app.application.business_analytics.workflow import (
    BusinessAnalyticsWorkflow,
)
from backend.app.application.text2sql.models import SQLEngine
from backend.app.infrastructure.business_analytics.delivery_v2 import (
    BusinessDataDeliveryV2Consumer,
    FileBusinessDataDeliveryResolver,
)
from backend.app.infrastructure.business_data.contract_catalog import (
    BusinessDataContractCatalog,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "business_data" / "delivery_v2"
DATASET_NAMES = ("orders_v1", "tickets_v1", "ticket_events_v1", "actions_v1")


def make_context(
    *,
    tenant_id: str = "tenant-synthetic",
    allowed_datasets: tuple[str, ...] = ("orders_v1",),
) -> BusinessAnalyticsContext:
    return BusinessAnalyticsContext(
        request_id="request-synthetic-001",
        tenant_id=tenant_id,
        contract_name="supportops_business_data",
        contract_version="v1",
        allowed_datasets=allowed_datasets,
    )


def make_effective_catalog(
    context: BusinessAnalyticsContext,
    *,
    requested_datasets: tuple[str, ...] | None = None,
):
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    request = BusinessAnalyticsRequest(
        question="Count order statuses",
        requested_datasets=requested_datasets or context.allowed_datasets,
        engine=SQLEngine.HIVE,
    )
    prepared = BusinessAnalyticsWorkflow(catalog_port=catalog_port).prepare(
        request=request,
        context=context,
    )
    return build_effective_catalog(
        prepared,
        engine=request.engine,
        policy=ManagedAnalyticsPolicy(),
    )


def make_consumer(delivery_path: Path) -> BusinessDataDeliveryV2Consumer:
    return BusinessDataDeliveryV2Consumer(
        delivery_resolver=FileBusinessDataDeliveryResolver(
            tenant_delivery_directories={"tenant-synthetic": delivery_path}
        ),
        contract_catalog=BusinessDataContractCatalog.from_repository_root(
            REPOSITORY_ROOT
        ),
    )


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def canonical_json_line(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def copy_fixture(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "delivery"
    shutil.copytree(FIXTURE, target)
    return target


def refresh_descriptor_and_snapshot(delivery: Path, dataset_name: str) -> None:
    manifest_path = delivery / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = next(
        item for item in manifest["datasets"] if item["dataset_name"] == dataset_name
    )
    content = (delivery / descriptor["file_name"]).read_bytes()
    descriptor["content_sha256"] = hashlib.sha256(content).hexdigest()
    descriptor["row_count"] = len(content.splitlines())
    material = {
        "contract_sha256": manifest["schema_sha256"],
        "contract_version": manifest["contract_version"],
        "currency": manifest["currency"],
        "datasets": [
            {
                "content_sha256": item["content_sha256"],
                "dataset_name": item["dataset_name"],
                "row_count": item["row_count"],
            }
            for item in manifest["datasets"]
        ],
        "tenant_id": manifest["tenant_id"],
    }
    manifest["snapshot"]["snapshot_id"] = hashlib.sha256(
        canonical_json(material)
    ).hexdigest()
    manifest_path.write_bytes(canonical_json(manifest))


def rewrite_first_record(
    delivery: Path,
    dataset_name: str,
    update: dict[str, object],
    *,
    duplicate_key: bool = False,
) -> None:
    filename = f"{dataset_name}.jsonl"
    path = delivery / filename
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0].update(update)
    if duplicate_key:
        record = records[0]
        pairs = list(record.items()) + [(next(iter(update)), record[next(iter(update))])]
        encoded_pairs = ",".join(
            json.dumps(key) + ":" + json.dumps(value) for key, value in pairs
        )
        line = ("{" + encoded_pairs + "}\n").encode("utf-8")
        path.write_bytes(line + b"".join(canonical_json_line(item) for item in records[1:]))
    else:
        path.write_bytes(b"".join(canonical_json_line(record) for record in records))
    refresh_descriptor_and_snapshot(delivery, dataset_name)


def test_consumer_validates_synthetic_delivery_and_keeps_only_effective_metadata() -> None:
    context = make_context()
    delivery = make_consumer(FIXTURE).validate(
        context=context,
        catalog=make_effective_catalog(context),
        policy=GovernedExecutionPolicy(),
    )

    snapshot = delivery.snapshot
    assert snapshot.tenant_id == context.tenant_id
    assert snapshot.currency == "USD"
    assert snapshot.contract_sha256 == (
        "7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b"
    )
    assert snapshot.effective_datasets == ("orders_v1",)
    assert [(item.dataset_name, item.row_count) for item in snapshot.datasets] == [
        ("orders_v1", 1)
    ]
    manifest_bytes = (FIXTURE / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    ordered_hashes = b"\n".join(
        item["content_sha256"].encode("ascii") for item in manifest["datasets"]
    )
    assert snapshot.delivery_fingerprint == hashlib.sha256(
        manifest_bytes + b"\n" + ordered_hashes
    ).hexdigest()
    snapshot_material = {
        "contract_sha256": manifest["schema_sha256"],
        "contract_version": manifest["contract_version"],
        "currency": manifest["currency"],
        "datasets": [
            {
                "content_sha256": item["content_sha256"],
                "dataset_name": item["dataset_name"],
                "row_count": item["row_count"],
            }
            for item in manifest["datasets"]
        ],
        "tenant_id": manifest["tenant_id"],
    }
    assert snapshot.snapshot_id == hashlib.sha256(
        canonical_json(snapshot_material)
    ).hexdigest()
    assert str(FIXTURE) not in snapshot.model_dump_json()


@pytest.mark.parametrize(
    "mutation",
    [
        "contract_hash",
        "wrong_content_hash",
        "row_count",
        "snapshot_id",
        "watermarks",
        "path_traversal",
        "duplicate_dataset",
    ],
)
def test_consumer_rejects_tampered_manifest(tmp_path: Path, mutation: str) -> None:
    delivery = copy_fixture(tmp_path)
    manifest_path = delivery / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "contract_hash":
        manifest["schema_sha256"] = "0" * 64
    elif mutation == "wrong_content_hash":
        manifest["datasets"][0]["content_sha256"] = "0" * 64
    elif mutation == "row_count":
        manifest["datasets"][0]["row_count"] += 1
    elif mutation == "snapshot_id":
        manifest["snapshot"]["snapshot_id"] = "0" * 64
    elif mutation == "watermarks":
        manifest["snapshot"]["watermarks"]["tickets_v1"]["max_value"] = None
    elif mutation == "path_traversal":
        manifest["datasets"][0]["file_name"] = "../orders_v1.jsonl"
    elif mutation == "duplicate_dataset":
        manifest["datasets"][1]["dataset_name"] = "orders_v1"
    manifest_path.write_bytes(canonical_json(manifest))

    context = make_context()
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(),
        )


def test_manifest_tenant_mismatch_and_duplicate_manifest_key_fail_closed(
    tmp_path: Path,
) -> None:
    context = make_context()
    delivery = copy_fixture(tmp_path)
    manifest_path = delivery / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tenant_id"] = "tenant-other"
    manifest_path.write_bytes(canonical_json(manifest))
    with pytest.raises(BusinessDataTenantMismatchError):
        make_consumer(delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(),
        )

    duplicate_delivery = copy_fixture(tmp_path / "duplicate")
    duplicate_manifest = duplicate_delivery / "manifest.json"
    content = duplicate_manifest.read_bytes()
    content = content.replace(
        b'"tenant_id": "tenant-synthetic"',
        b'"tenant_id": "tenant-synthetic",\n  "tenant_id": "tenant-other"',
    )
    duplicate_manifest.write_bytes(content)
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(duplicate_delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(),
        )


@pytest.mark.parametrize(
    ("dataset_name", "update"),
    [
        ("orders_v1", {"tenant_id": "tenant-attacker"}),
        ("orders_v1", {"amount": "1e2"}),
        ("orders_v1", {"unknown_field": "no"}),
        ("orders_v1", {"status": None}),
        ("tickets_v1", {"status": "not-an-enum"}),
        ("tickets_v1", {"created_at": "2026-09-24T00:00:00+00:00"}),
    ],
)
def test_consumer_rejects_invalid_dataset_records(
    tmp_path: Path,
    dataset_name: str,
    update: dict[str, object],
) -> None:
    delivery = copy_fixture(tmp_path)
    rewrite_first_record(delivery, dataset_name, update)
    context = make_context()
    error = (
        BusinessDataTenantMismatchError
        if "tenant_id" in update
        else BusinessDataIntegrityError
    )
    with pytest.raises(error):
        make_consumer(delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(),
        )


def test_consumer_rejects_duplicate_json_key_and_row_budget(tmp_path: Path) -> None:
    delivery = copy_fixture(tmp_path)
    rewrite_first_record(
        delivery,
        "orders_v1",
        {"amount": "12.30"},
        duplicate_key=True,
    )
    context = make_context()
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(),
        )


def test_consumer_rejects_duplicate_primary_keys_and_unexpected_entries(
    tmp_path: Path,
) -> None:
    delivery = copy_fixture(tmp_path)
    content = (delivery / "orders_v1.jsonl").read_bytes()
    (delivery / "orders_v1.jsonl").write_bytes(content + content)
    manifest_path = delivery / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = manifest["datasets"][0]
    descriptor["row_count"] = 2
    descriptor["content_sha256"] = hashlib.sha256(content + content).hexdigest()
    material = {
        "contract_sha256": manifest["schema_sha256"],
        "contract_version": manifest["contract_version"],
        "currency": manifest["currency"],
        "datasets": [
            {
                "content_sha256": item["content_sha256"],
                "dataset_name": item["dataset_name"],
                "row_count": item["row_count"],
            }
            for item in manifest["datasets"]
        ],
        "tenant_id": manifest["tenant_id"],
    }
    manifest["snapshot"]["snapshot_id"] = hashlib.sha256(
        canonical_json(material)
    ).hexdigest()
    manifest_path.write_bytes(canonical_json(manifest))
    context = make_context()
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(),
        )

    extra_delivery = copy_fixture(tmp_path / "extra")
    (extra_delivery / "unexpected.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(extra_delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(),
        )

    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(FIXTURE).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(max_total_input_rows=3),
        )


def test_consumer_rejects_input_and_record_size_budgets(tmp_path: Path) -> None:
    context = make_context()
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(FIXTURE).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(max_input_bytes=1024),
        )

    delivery = copy_fixture(tmp_path)
    rewrite_first_record(delivery, "orders_v1", {"status": "x" * 500})
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(max_record_bytes=256),
        )


def test_consumer_rejects_manifest_and_per_dataset_row_budgets(tmp_path: Path) -> None:
    context = make_context()
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(FIXTURE).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(max_manifest_bytes=1024),
        )

    delivery = copy_fixture(tmp_path)
    manifest_path = delivery / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["datasets"][0]["row_count"] = 2
    manifest_path.write_bytes(canonical_json(manifest))
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(max_input_rows_per_dataset=1),
        )


def test_consumer_rejects_symlinked_dataset_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = copy_fixture(tmp_path)
    orders_path = delivery / "orders_v1.jsonl"
    original_is_symlink = Path.is_symlink

    def detect_symlink(path: Path) -> bool:
        return path == orders_path or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", detect_symlink)

    context = make_context()
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(),
        )


@pytest.mark.skipif(os.name == "nt", reason="real POSIX symlink integration requires a POSIX runner")
def test_consumer_rejects_real_symlinked_dataset_file(tmp_path: Path) -> None:
    delivery = copy_fixture(tmp_path)
    orders_path = delivery / "orders_v1.jsonl"
    target_path = tmp_path / "outside-orders.jsonl"
    target_path.write_bytes(orders_path.read_bytes())
    orders_path.unlink()
    orders_path.symlink_to(target_path)

    context = make_context()
    with pytest.raises(BusinessDataIntegrityError):
        make_consumer(delivery).validate(
            context=context,
            catalog=make_effective_catalog(context),
            policy=GovernedExecutionPolicy(),
        )


def test_only_effective_scope_is_reported_to_the_execution_snapshot() -> None:
    context = make_context(allowed_datasets=DATASET_NAMES)
    catalog = make_effective_catalog(
        context,
        requested_datasets=("orders_v1",),
    )
    delivery = make_consumer(FIXTURE).validate(
        context=context,
        catalog=catalog,
        policy=GovernedExecutionPolicy(),
    )
    assert delivery.snapshot.effective_datasets == ("orders_v1",)
    assert [dataset.dataset_name for dataset in delivery.snapshot.datasets] == ["orders_v1"]


def test_delivery_fingerprint_includes_manifest_but_snapshot_id_tracks_content(
    tmp_path: Path,
) -> None:
    context = make_context()
    first = make_consumer(FIXTURE).validate(
        context=context,
        catalog=make_effective_catalog(context),
        policy=GovernedExecutionPolicy(),
    ).snapshot
    delivery = copy_fixture(tmp_path)
    manifest_path = delivery / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated_at"] = "2026-09-24T00:05:00Z"
    manifest_path.write_bytes(canonical_json(manifest))
    second = make_consumer(delivery).validate(
        context=context,
        catalog=make_effective_catalog(context),
        policy=GovernedExecutionPolicy(),
    ).snapshot

    assert second.snapshot_id == first.snapshot_id
    assert second.delivery_fingerprint != first.delivery_fingerprint
