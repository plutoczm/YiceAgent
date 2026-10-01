import re

from backend.app.application.text2sql.models import (
    DatabaseSchema,
    SchemaColumn,
    TableSchema,
)


CREATE_TABLE_NAME_PATTERN = re.compile(
    r"create\s+(?:external\s+)?table\s+(?:if\s+not\s+exists\s+)?"
    r"(?P<name>[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)\s*",
    re.IGNORECASE | re.DOTALL,
)

SIMPLE_TABLE_NAME_PATTERN = re.compile(
    r"(?<![\w.])(?P<name>[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)\s*\(",
    re.IGNORECASE,
)

TABLE_COMMENT_PATTERN = re.compile(
    r"\bcomment\s+'(?P<comment>(?:''|[^'])*)'",
    re.IGNORECASE | re.DOTALL,
)


class SchemaService:
    def parse_schema_context(
        self,
        schema_context: str | None,
        *,
        database_name: str | None = None,
    ) -> DatabaseSchema:
        if not schema_context:
            return DatabaseSchema(database_name=database_name)

        tables: list[TableSchema] = []
        consumed_spans: list[tuple[int, int]] = []
        for match in CREATE_TABLE_NAME_PATTERN.finditer(schema_context):
            open_paren_index = schema_context.find("(", match.end())
            close_paren_index = self._find_matching_paren(schema_context, open_paren_index)
            if open_paren_index < 0 or close_paren_index < 0:
                continue
            table = self._parse_table_match(
                match.group("name"),
                schema_context[open_paren_index + 1:close_paren_index],
                default_database=database_name,
                description=self._parse_table_comment(
                    schema_context[close_paren_index + 1:]
                ),
            )
            tables.append(table)
            consumed_spans.append((match.start(), close_paren_index + 1))

        for match in SIMPLE_TABLE_NAME_PATTERN.finditer(schema_context):
            open_paren_index = schema_context.find("(", match.end() - 1)
            close_paren_index = self._find_matching_paren(schema_context, open_paren_index)
            if open_paren_index < 0 or close_paren_index < 0:
                continue
            span = (match.start(), close_paren_index + 1)
            if self._is_inside_consumed_span(span, consumed_spans):
                continue
            prefix = schema_context[max(0, match.start() - 20):match.start()].lower()
            if "table" in prefix:
                continue
            table = self._parse_table_match(
                match.group("name"),
                schema_context[open_paren_index + 1:close_paren_index],
                default_database=database_name,
            )
            if table.columns:
                tables.append(table)

        return DatabaseSchema(
            database_name=database_name,
            tables=self._deduplicate_tables(tables),
        )

    def _parse_table_match(
        self,
        raw_name: str,
        body: str,
        *,
        default_database: str | None,
        description: str | None = None,
    ) -> TableSchema:
        database, table_name = self._split_table_name(raw_name)
        columns = self._parse_columns(body)
        return TableSchema(
            name=table_name,
            database=database or default_database,
            columns=columns,
            description=description,
        )

    def _parse_columns(self, body: str) -> list[SchemaColumn]:
        columns: list[SchemaColumn] = []
        for item in self._split_column_items(body):
            cleaned = item.strip().rstrip(",")
            if not cleaned:
                continue
            first = cleaned.split(maxsplit=1)[0].strip("`").lower()
            if first in {
                "primary",
                "key",
                "index",
                "constraint",
                "partitioned",
                "clustered",
                "stored",
                "comment",
            }:
                continue
            column = self._parse_column(cleaned)
            if column is not None:
                columns.append(column)
        return columns

    def _parse_column(self, item: str) -> SchemaColumn | None:
        match = re.match(
            r"`?(?P<name>[a-zA-Z_][\w]*)`?\s+"
            r"(?P<type>[a-zA-Z]+(?:\s*\([^)]*\))?)"
            r"(?:\s+comment\s+'(?P<comment>(?:''|[^'])*)')?",
            item,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        return SchemaColumn(
            name=match.group("name"),
            data_type=re.sub(r"\s+", "", match.group("type")).lower(),
            description=(
                match.group("comment").replace("''", "'")
                if match.group("comment") is not None
                else None
            ),
        )

    def _parse_table_comment(self, suffix: str) -> str | None:
        match = TABLE_COMMENT_PATTERN.search(suffix)
        if match is None:
            return None
        return match.group("comment").replace("''", "'")

    def _split_column_items(self, body: str) -> list[str]:
        items: list[str] = []
        current: list[str] = []
        depth = 0
        quote: str | None = None
        index = 0
        while index < len(body):
            char = body[index]
            if quote is not None:
                current.append(char)
                if char == quote:
                    if index + 1 < len(body) and body[index + 1] == quote:
                        current.append(body[index + 1])
                        index += 2
                        continue
                    quote = None
                elif char == "\\" and index + 1 < len(body):
                    current.append(body[index + 1])
                    index += 2
                    continue
                index += 1
                continue
            if char in {"'", '"', "`"}:
                quote = char
                current.append(char)
            elif char == "(":
                depth += 1
                current.append(char)
            elif char == ")":
                depth = max(0, depth - 1)
                current.append(char)
            elif char == "," and depth == 0:
                items.append("".join(current))
                current = []
            else:
                current.append(char)
            index += 1
        if current:
            items.append("".join(current))
        return items

    def _split_table_name(self, raw_name: str) -> tuple[str | None, str]:
        parts = [part.strip("`") for part in raw_name.split(".")]
        if len(parts) == 2:
            return parts[0], parts[1]
        return None, parts[0]

    def _deduplicate_tables(self, tables: list[TableSchema]) -> list[TableSchema]:
        seen: set[str] = set()
        deduplicated: list[TableSchema] = []
        for table in tables:
            key = table.full_name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(table)
        return deduplicated

    def _is_inside_consumed_span(
        self,
        span: tuple[int, int],
        consumed_spans: list[tuple[int, int]],
    ) -> bool:
        start, end = span
        return any(
            start >= used_start and end <= used_end
            for used_start, used_end in consumed_spans
        )

    def _find_matching_paren(self, text: str, open_paren_index: int) -> int:
        if open_paren_index < 0:
            return -1
        depth = 0
        quote: str | None = None
        index = open_paren_index
        while index < len(text):
            char = text[index]
            if quote is not None:
                if char == quote:
                    if index + 1 < len(text) and text[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
                elif char == "\\" and index + 1 < len(text):
                    index += 2
                    continue
                index += 1
                continue
            if char in {"'", '"', "`"}:
                quote = char
            else:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        return index
            index += 1
        return -1
