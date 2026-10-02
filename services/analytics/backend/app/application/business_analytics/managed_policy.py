from backend.app.application.business_analytics.errors import (
    BusinessCatalogMismatchError,
    BusinessSQLPolicyViolationError,
    BusinessScopeViolationError,
)
from backend.app.application.business_analytics.managed_models import (
    EffectiveBusinessCatalog,
    ManagedAnalyticsPolicy,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsResult,
    BusinessDataset,
)
from backend.app.application.text2sql.models import SQLEngine


def build_effective_catalog(
    prepared: BusinessAnalyticsResult,
    *,
    engine: SQLEngine,
    policy: ManagedAnalyticsPolicy,
) -> EffectiveBusinessCatalog:
    """Apply trusted dataset and classification policy to the prepared snapshot."""

    snapshot = prepared.catalog_snapshot
    selected_names = set(prepared.resolved_datasets)
    available = {dataset.logical_name: dataset for dataset in snapshot.datasets}
    if not selected_names or not selected_names.issubset(available):
        raise BusinessCatalogMismatchError()

    selected = {name: available[name] for name in selected_names}
    filtered_fields = {
        dataset_name: tuple(
            field
            for field in dataset.fields
            if field.classification in policy.allowed_classifications
        )
        for dataset_name, dataset in selected.items()
    }
    if any(not fields for fields in filtered_fields.values()):
        raise BusinessSQLPolicyViolationError()

    fields_by_dataset = {
        dataset_name: {field.name for field in fields}
        for dataset_name, fields in filtered_fields.items()
    }
    effective_datasets: list[BusinessDataset] = []
    for dataset_name in sorted(selected):
        dataset = selected[dataset_name]
        effective_fields = fields_by_dataset[dataset_name]
        fields = tuple(
            field.model_copy(
                update={
                    "references": (
                        field.references
                        if field.references is not None
                        and field.references.target_dataset in selected
                        and set(field.references.target_fields).issubset(
                            fields_by_dataset[field.references.target_dataset]
                        )
                        else None
                    )
                }
            )
            for field in filtered_fields[dataset_name]
        )
        relationships = tuple(
            relation
            for relation in dataset.relationships
            if relation.target_dataset in selected
            and set(relation.source_fields).issubset(effective_fields)
            and set(relation.target_fields).issubset(
                fields_by_dataset[relation.target_dataset]
            )
        )
        primary_key = (
            dataset.primary_key
            if dataset.primary_key is not None
            and set(dataset.primary_key.fields).issubset(effective_fields)
            else None
        )
        effective_datasets.append(
            BusinessDataset(
                logical_name=dataset.logical_name,
                description=dataset.description,
                grain=dataset.grain,
                fields=fields,
                relationships=relationships,
                primary_key=primary_key,
                classification=dataset.classification,
                stability=dataset.stability,
            )
        )

    try:
        return EffectiveBusinessCatalog(
            request_id=prepared.request_id,
            tenant_id=snapshot.tenant_id,
            contract_name=snapshot.contract_name,
            contract_version=snapshot.contract_version,
            contract_sha256=snapshot.contract_sha256,
            catalog_fingerprint=snapshot.catalog_fingerprint,
            engine=engine,
            allowed_classifications=policy.allowed_classifications,
            datasets=tuple(effective_datasets),
        )
    except ValueError:
        raise BusinessScopeViolationError() from None
