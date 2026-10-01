from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from backend.app.application.business_analytics.governed_models import (
    BusinessDatasetSnapshotMetadata,
    GovernedResultColumn,
)
from backend.app.application.business_analytics.models import BusinessFieldClassification
from backend.app.application.text2sql.models import SQLEngine


JsonScalar = StrictStr | StrictInt | StrictBool | None


class BusinessAnalyticsAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=8000)
    engine: SQLEngine = SQLEngine.HIVE
    requested_datasets: tuple[str, ...] = ()


class SnapshotFreshnessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: str = Field(min_length=20, max_length=64)
    consistency: str = Field(min_length=1, max_length=64)
    datasets: tuple[BusinessDatasetSnapshotMetadata, ...]


class BusinessAnalyticsQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=128)
    status: Literal["executed"]
    columns: tuple[GovernedResultColumn, ...]
    rows: tuple[tuple[JsonScalar, ...], ...]
    row_count: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    snapshot_freshness: SnapshotFreshnessResponse
    result_classification: BusinessFieldClassification
    query_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_version: str = Field(min_length=1, max_length=64)
