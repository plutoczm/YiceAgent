from enum import StrEnum

from pydantic import BaseModel, Field

from backend.app.domain.ports.llm_provider import LLMUsage


class TableLayer(StrEnum):
    SOURCE = "SOURCE"
    ODS = "ODS"
    DWD = "DWD"
    DWS = "DWS"
    ADS = "ADS"
    DIM = "DIM"
    FACT = "FACT"


class WarehouseColumn(BaseModel):
    name: str
    data_type: str
    description: str


class WarehouseTable(BaseModel):
    name: str
    layer: TableLayer
    description: str
    columns: list[WarehouseColumn] = Field(default_factory=list)
    partition_columns: list[str] = Field(default_factory=lambda: ["dt"])
    primary_keys: list[str] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)


class TableRelationship(BaseModel):
    source_table: str
    target_table: str
    relationship_type: str
    join_keys: list[str] = Field(default_factory=list)
    description: str


class MetricDefinition(BaseModel):
    name: str
    definition: str
    calculation_logic: str
    business_meaning: str
    source_tables: list[str] = Field(default_factory=list)


class DDLStatement(BaseModel):
    table_name: str
    layer: TableLayer
    sql: str


class DataFlowDesign(BaseModel):
    flow: list[str] = Field(default_factory=lambda: ["ODS", "DWD", "DWS", "ADS"])
    pipeline_description: str
    dependency_graph: dict[str, list[str]]


class WarehouseDesignResult(BaseModel):
    requirement: str
    source_tables: list[WarehouseTable] = Field(default_factory=list)
    ods: list[WarehouseTable] = Field(default_factory=list)
    dwd: list[WarehouseTable] = Field(default_factory=list)
    dws: list[WarehouseTable] = Field(default_factory=list)
    ads: list[WarehouseTable] = Field(default_factory=list)
    dim: list[WarehouseTable] = Field(default_factory=list)
    fact_tables: list[WarehouseTable] = Field(default_factory=list)
    relationships: list[TableRelationship] = Field(default_factory=list)
    ddl: list[DDLStatement] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)
    data_flow: DataFlowDesign
    recommendations: list[str] = Field(default_factory=list)
    token_usage: LLMUsage = Field(default_factory=LLMUsage)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @classmethod
    def example(cls, requirement: str) -> "WarehouseDesignResult":
        from backend.app.application.warehouse_design.design_service import (
            WarehouseDesignService,
        )

        return WarehouseDesignService.build_template_design(requirement)
