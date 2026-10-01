import re

from backend.app.application.text2sql.models import (
    DatabaseSchema,
    SQLEngine,
    SQLValidationIssue,
    SQLValidationResult,
)


TABLE_REFERENCE_PATTERN = re.compile(
    r"\b(?:from|join)\s+(`?[a-zA-Z_][\w]*`?(?:\.`?[a-zA-Z_][\w]*`?)?)"
    r"(?:\s+(?:as\s+)?([a-zA-Z_][\w]*))?",
    re.IGNORECASE,
)

QUALIFIED_COLUMN_PATTERN = re.compile(
    r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\b",
    re.IGNORECASE,
)

FORBIDDEN_KEYWORDS = {
    "alter",
    "call",
    "copy",
    "create",
    "delete",
    "drop",
    "exec",
    "execute",
    "grant",
    "insert",
    "load",
    "merge",
    "replace",
    "revoke",
    "truncate",
    "unload",
    "update",
}

DEFAULT_MAX_ROWS = 500


class SQLValidator:
    def validate(
        self,
        sql: str,
        *,
        schema: DatabaseSchema,
        engine: SQLEngine,
    ) -> SQLValidationResult:
        issues: list[SQLValidationIssue] = []
        normalized_sql = self._normalize_sql(sql)
        masked_sql, syntax_issues = self._mask_and_check_syntax(sql)
        issues.extend(syntax_issues)

        if not self._has_valid_structure(normalized_sql):
            issues.append(
                SQLValidationIssue(
                    code="invalid_sql_structure",
                    severity="error",
                    message="SQL must be a SELECT query for Text2SQL generation.",
                )
            )

        issues.extend(self._detect_unsafe_statements(masked_sql))
        issues.extend(self._validate_limit(masked_sql))
        table_aliases, table_issues = self._extract_table_aliases(sql, schema)
        issues.extend(table_issues)
        issues.extend(self._detect_unknown_columns(sql, schema, table_aliases))
        issues.extend(self._detect_select_star(normalized_sql))
        issues.extend(self._detect_join_issues(normalized_sql))
        issues.extend(self._detect_engine_warnings(normalized_sql, engine))

        is_valid = not any(issue.severity == "error" for issue in issues)
        return SQLValidationResult(is_valid=is_valid, issues=issues)

    def enforce_row_limit(self, sql: str, *, max_rows: int = DEFAULT_MAX_ROWS) -> str:
        if max_rows < 1:
            raise ValueError("max_rows must be greater than zero")
        statement = sql.strip().removesuffix(";").rstrip()
        masked_sql, syntax_issues = self._mask_and_check_syntax(statement)
        if syntax_issues:
            return statement

        mysql_limit = re.search(
            r"\blimit\s+(?P<offset>\d+)\s*,\s*(?P<count>\d+)\s*$",
            masked_sql,
            re.IGNORECASE,
        )
        if mysql_limit:
            count = min(int(mysql_limit.group("count")), max_rows)
            start, end = mysql_limit.span("count")
            return f"{statement[:start]}{count}{statement[end:]}"

        standard_limit = re.search(
            r"\blimit\s+(?P<count>\d+)(?:\s+offset\s+\d+)?\s*$",
            masked_sql,
            re.IGNORECASE,
        )
        if standard_limit:
            count = min(int(standard_limit.group("count")), max_rows)
            start, end = standard_limit.span("count")
            return f"{statement[:start]}{count}{statement[end:]}"

        if re.search(r"\blimit\b", masked_sql, re.IGNORECASE):
            return statement
        return f"{statement} LIMIT {max_rows}"

    def _normalize_sql(self, sql: str) -> str:
        return re.sub(r"\s+", " ", sql.strip()).lower()

    def _has_valid_structure(self, normalized_sql: str) -> bool:
        if not normalized_sql:
            return False
        return normalized_sql.startswith("select") or normalized_sql.startswith("with")

    def _detect_unsafe_statements(self, masked_sql: str) -> list[SQLValidationIssue]:
        issues: list[SQLValidationIssue] = []
        for keyword in sorted(FORBIDDEN_KEYWORDS):
            if not re.search(rf"\b{keyword}\b", masked_sql, re.IGNORECASE):
                continue
            code = f"dangerous_{keyword}"
            issues.append(
                SQLValidationIssue(
                    code=code,
                    severity="error",
                    message=f"{keyword.upper()} statements are not allowed.",
                )
            )
        if re.search(r"\bselect\b[\s\S]*\binto\b", masked_sql, re.IGNORECASE):
            issues.append(
                SQLValidationIssue(
                    code="dangerous_select_into",
                    severity="error",
                    message="SELECT INTO is not allowed in generated read-only SQL.",
                )
            )
        return issues

    def _mask_and_check_syntax(
        self,
        sql: str,
    ) -> tuple[str, list[SQLValidationIssue]]:
        masked = list(sql)
        issues: list[SQLValidationIssue] = []
        quote: str | None = None
        depth = 0
        index = 0
        while index < len(sql):
            char = sql[index]
            if quote is not None:
                masked[index] = " "
                if char == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        masked[index + 1] = " "
                        index += 2
                        continue
                    quote = None
                elif char == "\\" and index + 1 < len(sql):
                    masked[index + 1] = " "
                    index += 2
                    continue
                index += 1
                continue
            if char in {"'", '"', "`"}:
                quote = char
                masked[index] = " "
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    break
            index += 1

        if quote is not None or depth != 0:
            issues.append(
                SQLValidationIssue(
                    code="invalid_sql_syntax",
                    severity="error",
                    message="SQL contains unbalanced quotes or parentheses.",
                )
            )

        masked_sql = "".join(masked)
        if re.search(r"--|/\*|\*/|#", masked_sql):
            issues.append(
                SQLValidationIssue(
                    code="sql_comments_not_allowed",
                    severity="error",
                    message="SQL comments are not allowed in generated queries.",
                )
            )

        without_trailing_semicolon = masked_sql.strip().removesuffix(";")
        if ";" in without_trailing_semicolon:
            issues.append(
                SQLValidationIssue(
                    code="multiple_statements_not_allowed",
                    severity="error",
                    message="Only one SQL statement is allowed.",
                )
            )
        if masked_sql.strip().lower().startswith("with") and not re.search(
            r"\bselect\b", masked_sql, re.IGNORECASE
        ):
            issues.append(
                SQLValidationIssue(
                    code="invalid_sql_syntax",
                    severity="error",
                    message="A WITH query must contain a SELECT statement.",
                )
            )
        return masked_sql, issues

    def _validate_limit(self, masked_sql: str) -> list[SQLValidationIssue]:
        if not re.search(r"\blimit\b", masked_sql, re.IGNORECASE):
            return []
        valid_limit = re.search(
            r"\blimit\s+\d+(?:(?:\s*,\s*|\s+offset\s+)\d+)?\s*;?\s*$",
            masked_sql,
            re.IGNORECASE,
        )
        if valid_limit:
            return []
        return [
            SQLValidationIssue(
                code="invalid_limit",
                severity="error",
                message="LIMIT must use a non-negative integer literal at the end of the query.",
            )
        ]

    def _extract_table_aliases(
        self,
        sql: str,
        schema: DatabaseSchema,
    ) -> tuple[dict[str, str], list[SQLValidationIssue]]:
        aliases: dict[str, str] = {}
        issues: list[SQLValidationIssue] = []
        for match in TABLE_REFERENCE_PATTERN.finditer(sql):
            raw_table = self._clean_identifier(match.group(1))
            alias = match.group(2)
            table_key = raw_table.lower()
            short_name = table_key.split(".")[-1]
            if table_key not in schema.table_names and short_name not in schema.table_names:
                issues.append(
                    SQLValidationIssue(
                        code="unknown_table",
                        severity="error",
                        message=f"Unknown table '{raw_table}'.",
                        object_name=raw_table,
                    )
                )
                continue
            aliases[short_name] = short_name
            if alias and alias.lower() not in {"where", "on", "group", "order", "limit", "join"}:
                aliases[alias.lower()] = short_name
        return aliases, issues

    def _detect_unknown_columns(
        self,
        sql: str,
        schema: DatabaseSchema,
        table_aliases: dict[str, str],
    ) -> list[SQLValidationIssue]:
        issues: list[SQLValidationIssue] = []
        for alias, column in QUALIFIED_COLUMN_PATTERN.findall(sql):
            normalized_alias = alias.lower()
            if normalized_alias not in table_aliases:
                continue
            table_name = table_aliases[normalized_alias]
            try:
                table = schema.require_table(table_name)
            except KeyError:
                continue
            if column.lower() not in table.column_names:
                issues.append(
                    SQLValidationIssue(
                        code="unknown_column",
                        severity="error",
                        message=f"Unknown column '{alias}.{column}'.",
                        object_name=f"{alias}.{column}",
                    )
                )
        return issues

    def _detect_select_star(self, normalized_sql: str) -> list[SQLValidationIssue]:
        if re.search(r"\bselect\s+\*", normalized_sql) or re.search(r"\b[a-zA-Z_][\w]*\.\*", normalized_sql):
            return [
                SQLValidationIssue(
                    code="select_star",
                    severity="warning",
                    message="Avoid SELECT * in production analytical SQL.",
                )
            ]
        return []

    def _detect_join_issues(self, normalized_sql: str) -> list[SQLValidationIssue]:
        issues: list[SQLValidationIssue] = []
        join_count = len(re.findall(r"\bjoin\b", normalized_sql))
        if join_count == 0:
            from_clause = self._extract_from_clause(normalized_sql)
            if self._has_top_level_comma(from_clause):
                issues.append(
                    SQLValidationIssue(
                        code="cartesian_join",
                        severity="error",
                        message="Comma-separated table joins can create Cartesian joins.",
                    )
                )
            return issues

        on_count = len(re.findall(r"\bon\b", normalized_sql))
        using_count = len(re.findall(r"\busing\s*\(", normalized_sql))
        if on_count + using_count < join_count:
            issues.append(
                SQLValidationIssue(
                    code="missing_join_condition",
                    severity="error",
                    message="Every JOIN must include an ON or USING condition.",
                )
            )
        if re.search(r"\bcross\s+join\b", normalized_sql):
            issues.append(
                SQLValidationIssue(
                    code="cartesian_join",
                    severity="warning",
                    message="CROSS JOIN may create a Cartesian product.",
                )
            )
        return issues

    def _extract_from_clause(self, normalized_sql: str) -> str:
        match = re.search(
            r"\bfrom\b(?P<from_clause>.*?)(?:\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\blimit\b|$)",
            normalized_sql,
        )
        if match is None:
            return ""
        return match.group("from_clause")

    def _has_top_level_comma(self, clause: str) -> bool:
        depth = 0
        quote: str | None = None
        for char in clause:
            if char in {"'", '"'}:
                quote = None if quote == char else char
            elif quote is None:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth = max(0, depth - 1)
                elif char == "," and depth == 0:
                    return True
        return False

    def _detect_engine_warnings(
        self,
        normalized_sql: str,
        engine: SQLEngine,
    ) -> list[SQLValidationIssue]:
        issues: list[SQLValidationIssue] = []
        if engine in {SQLEngine.HIVE, SQLEngine.SPARK_SQL} and " where " not in f" {normalized_sql} ":
            issues.append(
                SQLValidationIssue(
                    code="missing_partition_filter",
                    severity="warning",
                    message="Consider partition pruning with a WHERE filter for large tables.",
                )
            )
        if engine is SQLEngine.CLICKHOUSE and "prewhere" not in normalized_sql and " where " in f" {normalized_sql} ":
            issues.append(
                SQLValidationIssue(
                    code="clickhouse_prewhere_candidate",
                    severity="warning",
                    message="Consider PREWHERE for highly selective ClickHouse filters.",
                )
            )
        return issues

    def _clean_identifier(self, value: str) -> str:
        return value.replace("`", "")
