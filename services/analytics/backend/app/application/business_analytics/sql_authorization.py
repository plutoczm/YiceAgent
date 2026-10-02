from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from backend.app.application.business_analytics.errors import (
    BusinessSQLAuthorizationError,
    BusinessSQLParseError,
    BusinessSQLPolicyViolationError,
)
from backend.app.application.business_analytics.managed_models import (
    EffectiveBusinessCatalog,
    ManagedGenericValidation,
    ManagedSQLReferencedField,
)
from backend.app.application.business_analytics.models import BusinessLogicalType
from backend.app.application.text2sql.models import (
    SQLEngine,
)


DIALECT_BY_ENGINE: dict[SQLEngine, str] = {
    SQLEngine.HIVE: "hive",
    SQLEngine.SPARK_SQL: "spark",
    SQLEngine.MYSQL: "mysql",
    SQLEngine.CLICKHOUSE: "clickhouse",
}

EXTERNAL_SOURCE_FUNCTIONS = {
    "cluster",
    "file",
    "glob",
    "hdfs",
    "input",
    "jdbc",
    "load_file",
    "mysql",
    "postgresql",
    "read_csv",
    "read_json",
    "read_parquet",
    "remote",
    "s3",
    "url",
}
FORBIDDEN_STATEMENT_NODE_NAMES = {
    "Alter",
    "Command",
    "Copy",
    "Create",
    "Delete",
    "Drop",
    "Execute",
    "Grant",
    "Insert",
    "LoadData",
    "Lock",
    "Merge",
    "Revoke",
    "Transaction",
    "TruncateTable",
    "Update",
}


@dataclass(frozen=True)
class ManagedSQLAuthorizationEvidence:
    referenced_datasets: tuple[str, ...]
    referenced_fields: tuple[ManagedSQLReferencedField, ...]
    cte_names: frozenset[str]


class ManagedSQLAuthorizer:
    """Dialect-aware AST gate for the already scoped logical catalog."""

    def authorize(
        self,
        *,
        sql: str,
        catalog: EffectiveBusinessCatalog,
        generic_validation: ManagedGenericValidation,
        max_rows: int | None = None,
        dialect_override: str | None = None,
    ) -> ManagedSQLAuthorizationEvidence:
        dialect = dialect_override or DIALECT_BY_ENGINE[catalog.engine]
        if dialect not in {*DIALECT_BY_ENGINE.values(), "duckdb"}:
            raise BusinessSQLAuthorizationError()
        expression = self._parse_single_read_query(sql, dialect)
        cte_names = frozenset(
            cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)
        )
        self._validate_generic_safety(generic_validation, cte_names)
        self._reject_forbidden_statements(expression)
        self._reject_wildcard_projection(expression)
        referenced_datasets = self._validate_table_scope(expression, catalog)
        qualified = self._qualify_columns(expression, catalog, dialect)
        referenced_fields = self._collect_referenced_fields(qualified)
        if max_rows is not None:
            self._validate_row_limits(expression, max_rows)
        return ManagedSQLAuthorizationEvidence(
            referenced_datasets=tuple(sorted(referenced_datasets)),
            referenced_fields=tuple(
                sorted(
                    referenced_fields,
                    key=lambda field: (field.dataset_name, field.field_name),
                )
            ),
            cte_names=cte_names,
        )

    def _parse_single_read_query(
        self,
        sql: str,
        dialect: str,
    ) -> exp.Expression:
        try:
            statements = sqlglot.parse(
                sql,
                read=dialect,
                error_level=ErrorLevel.RAISE,
            )
        except Exception:
            raise BusinessSQLParseError() from None
        if len(statements) != 1 or statements[0] is None:
            raise BusinessSQLPolicyViolationError()
        expression = statements[0]
        query_roots = (exp.Select, exp.Union, exp.Intersect, exp.Except)
        if not isinstance(expression, query_roots):
            raise BusinessSQLPolicyViolationError()
        if any(
            type(node).__name__ in FORBIDDEN_STATEMENT_NODE_NAMES
            for node in expression.walk()
        ):
            raise BusinessSQLPolicyViolationError()
        return expression

    def _validate_generic_safety(
        self,
        generic_validation: ManagedGenericValidation,
        cte_names: frozenset[str],
    ) -> None:
        errors = [
            issue
            for issue in generic_validation.issues
            if issue.severity == "error"
            and not (
                issue.code == "unknown_table"
                and issue.object_name is not None
                and issue.object_name.lower() in cte_names
            )
        ]
        has_cte_false_positive = any(
            issue.code == "unknown_table"
            and issue.object_name is not None
            and issue.object_name.lower() in cte_names
            for issue in generic_validation.issues
        )
        if errors or (not generic_validation.is_valid and not has_cte_false_positive):
            raise BusinessSQLPolicyViolationError()

    def _reject_forbidden_statements(self, expression: exp.Expression) -> None:
        if any(
            isinstance(node, exp.Table)
            and isinstance(node.this, exp.Func)
            for node in expression.walk()
        ):
            raise BusinessSQLAuthorizationError()
        for node in expression.walk():
            if isinstance(node, exp.Into) or isinstance(node, exp.Lock):
                raise BusinessSQLPolicyViolationError()
            if isinstance(node, exp.Anonymous):
                # Unknown/UDF calls may reach external data or privileged runtime
                # functions, so managed SQL accepts only parser-known functions.
                raise BusinessSQLAuthorizationError()
            if isinstance(node, exp.Func):
                function_name = node.sql_name().lower()
                if function_name in EXTERNAL_SOURCE_FUNCTIONS:
                    raise BusinessSQLAuthorizationError()

    def _reject_wildcard_projection(self, expression: exp.Expression) -> None:
        for select in expression.find_all(exp.Select):
            for projection in select.expressions:
                for star in projection.find_all(exp.Star):
                    if not (
                        isinstance(star.parent, exp.Count)
                        and star.parent.this is star
                    ):
                        raise BusinessSQLPolicyViolationError()
                if isinstance(projection, exp.Star):
                    raise BusinessSQLPolicyViolationError()

    def _validate_table_scope(
        self,
        expression: exp.Expression,
        catalog: EffectiveBusinessCatalog,
    ) -> set[str]:
        allowed = {dataset.logical_name for dataset in catalog.datasets}
        for table in expression.find_all(exp.Table):
            if table.catalog or table.db:
                raise BusinessSQLAuthorizationError()
            if not isinstance(table.this, exp.Identifier):
                raise BusinessSQLAuthorizationError()

        referenced: set[str] = set()
        try:
            scopes = traverse_scope(expression)
            for scope in scopes:
                for _, (node, source) in scope.selected_sources.items():
                    if isinstance(source, Scope):
                        continue
                    if not isinstance(node, exp.Table) or not isinstance(
                        source, exp.Table
                    ):
                        raise BusinessSQLAuthorizationError()
                    if not isinstance(source.this, exp.Identifier):
                        raise BusinessSQLAuthorizationError()
                    dataset_name = source.name
                    if dataset_name not in allowed:
                        raise BusinessSQLAuthorizationError()
                    referenced.add(dataset_name)
        except BusinessSQLAuthorizationError:
            raise
        except Exception:
            raise BusinessSQLAuthorizationError() from None
        return referenced

    def _qualify_columns(
        self,
        expression: exp.Expression,
        catalog: EffectiveBusinessCatalog,
        dialect: str,
    ) -> exp.Expression:
        schema = MappingSchema(
            {
                dataset.logical_name: {
                    field.name: self._sql_type_hint(field.logical_type, catalog.engine)
                    for field in dataset.fields
                }
                for dataset in catalog.datasets
            },
            dialect=dialect,
            normalize=True,
        )
        try:
            return qualify(
                expression.copy(),
                dialect=dialect,
                schema=schema,
                expand_stars=False,
                infer_schema=False,
                allow_partial_qualification=False,
                validate_qualify_columns=True,
                quote_identifiers=False,
                identify=False,
            )
        except Exception:
            raise BusinessSQLAuthorizationError() from None

    def _collect_referenced_fields(
        self,
        expression: exp.Expression,
    ) -> set[ManagedSQLReferencedField]:
        referenced: set[ManagedSQLReferencedField] = set()
        try:
            for scope in traverse_scope(expression):
                for column in scope.columns:
                    if not column.table or isinstance(column.this, exp.Star):
                        continue
                    source = scope.sources.get(column.table)
                    if isinstance(source, Scope):
                        continue
                    if not isinstance(source, exp.Table) or not isinstance(
                        source.this, exp.Identifier
                    ):
                        raise BusinessSQLAuthorizationError()
                    referenced.add(
                        ManagedSQLReferencedField(
                            dataset_name=source.name,
                            field_name=column.name,
                        )
                    )
        except BusinessSQLAuthorizationError:
            raise
        except Exception:
            raise BusinessSQLAuthorizationError() from None
        return referenced

    def _validate_row_limits(
        self,
        expression: exp.Expression,
        max_rows: int,
    ) -> None:
        if not expression.args.get("limit"):
            raise BusinessSQLPolicyViolationError()
        limits = list(expression.find_all(exp.Limit))
        if not limits:
            raise BusinessSQLPolicyViolationError()
        for limit in limits:
            if limit.args.get("expressions") or limit.args.get("limit_options"):
                raise BusinessSQLPolicyViolationError()
            count = limit.expression
            if (
                not isinstance(count, exp.Literal)
                or count.is_string
                or not count.is_int
            ):
                raise BusinessSQLPolicyViolationError()
            if int(count.this) < 0 or int(count.this) > max_rows:
                raise BusinessSQLPolicyViolationError()

    def _sql_type_hint(
        self,
        logical_type: BusinessLogicalType,
        engine: SQLEngine,
    ) -> str:
        if engine is SQLEngine.CLICKHOUSE:
            mapping = {
                BusinessLogicalType.IDENTIFIER: "String",
                BusinessLogicalType.STRING: "String",
                BusinessLogicalType.ENUM: "String",
                BusinessLogicalType.DECIMAL: "Decimal",
                BusinessLogicalType.TIMESTAMP: "DateTime",
                BusinessLogicalType.DATETIME: "DateTime",
                BusinessLogicalType.DATE: "Date",
                BusinessLogicalType.BOOLEAN: "Bool",
                BusinessLogicalType.INTEGER: "Int64",
                BusinessLogicalType.JSON: "String",
            }
        elif engine is SQLEngine.MYSQL:
            mapping = {
                BusinessLogicalType.IDENTIFIER: "VARCHAR",
                BusinessLogicalType.STRING: "TEXT",
                BusinessLogicalType.ENUM: "TEXT",
                BusinessLogicalType.DECIMAL: "DECIMAL",
                BusinessLogicalType.TIMESTAMP: "TIMESTAMP",
                BusinessLogicalType.DATETIME: "DATETIME",
                BusinessLogicalType.DATE: "DATE",
                BusinessLogicalType.BOOLEAN: "BOOLEAN",
                BusinessLogicalType.INTEGER: "BIGINT",
                BusinessLogicalType.JSON: "JSON",
            }
        else:
            mapping = {
                BusinessLogicalType.IDENTIFIER: "STRING",
                BusinessLogicalType.STRING: "STRING",
                BusinessLogicalType.ENUM: "STRING",
                BusinessLogicalType.DECIMAL: "DECIMAL",
                BusinessLogicalType.TIMESTAMP: "TIMESTAMP",
                BusinessLogicalType.DATETIME: "TIMESTAMP",
                BusinessLogicalType.DATE: "DATE",
                BusinessLogicalType.BOOLEAN: "BOOLEAN",
                BusinessLogicalType.INTEGER: "BIGINT",
                BusinessLogicalType.JSON: "STRING",
            }
        try:
            return mapping[logical_type]
        except KeyError:
            raise BusinessSQLAuthorizationError() from None
