from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from backend.app.domain.ports.llm_provider import LLMUsage


class SQLEngine(StrEnum):
    MYSQL = "mysql"
    HIVE = "hive"
    SPARK_SQL = "spark_sql"
    CLICKHOUSE = "clickhouse"


class SchemaColumn(BaseModel):
    name: str
    data_type: str
    description: str | None = None


class TableSchema(BaseModel):
    name: str
    columns: list[SchemaColumn]
    database: str | None = None
    description: str | None = None

    @computed_field
    @property
    def column_names(self) -> set[str]:
        return {column.name.lower() for column in self.columns}

    @property
    def full_name(self) -> str:
        return f"{self.database}.{self.name}" if self.database else self.name

    def require_column(self, name: str) -> SchemaColumn:
        normalized = name.lower()
        for column in self.columns:
            if column.name.lower() == normalized:
                return column
        raise KeyError(name)


class DatabaseSchema(BaseModel):
    database_name: str | None = None
    tables: list[TableSchema] = Field(default_factory=list)

    @computed_field
    @property
    def table_names(self) -> set[str]:
        names: set[str] = set()
        for table in self.tables:
            names.add(table.name.lower())
            if table.database:
                names.add(f"{table.database.lower()}.{table.name.lower()}")
        return names

    def require_table(self, name: str) -> TableSchema:
        normalized = name.lower()
        short_name = normalized.split(".")[-1]
        for table in self.tables:
            if table.name.lower() == short_name:
                return table
            if table.database and f"{table.database.lower()}.{table.name.lower()}" == normalized:
                return table
        raise KeyError(name)

    def render_for_prompt(self) -> str:
        sections: list[str] = []
        for table in self.tables:
            lines = [f"{table.full_name}("]
            for column in table.columns:
                description = f" -- {column.description}" if column.description else ""
                lines.append(f"  {column.name} {column.data_type}{description}")
            lines.append(")")
            if table.description:
                lines.append(f"description: {table.description}")
            sections.append("\n".join(lines))
        return "\n\n".join(sections)


class SQLValidationIssue(BaseModel):
    code: str
    severity: str = Field(pattern="^(error|warning)$")
    message: str
    object_name: str | None = None


class SQLValidationResult(BaseModel):
    is_valid: bool
    issues: list[SQLValidationIssue] = Field(default_factory=list)

    def has_issue(self, code: str) -> bool:
        return any(issue.code == code for issue in self.issues)


class Text2SQLResult(BaseModel):
    sql: str
    explanation: str
    optimization_suggestions: list[str]
    engine: SQLEngine
    confidence: float = Field(ge=0.0, le=1.0)
    validation: SQLValidationResult
    token_usage: LLMUsage = Field(default_factory=LLMUsage)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
