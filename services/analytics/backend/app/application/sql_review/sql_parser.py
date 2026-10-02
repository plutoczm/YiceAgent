import re

from pydantic import BaseModel, Field


class ParsedSQL(BaseModel):
    original_sql: str
    normalized_sql: str
    is_select: bool
    has_where: bool
    has_limit: bool
    has_order_by: bool
    has_group_by: bool
    join_count: int = Field(ge=0)
    join_condition_count: int = Field(ge=0)
    distinct_count: int = Field(ge=0)
    count_distinct_count: int = Field(ge=0)
    subquery_count: int = Field(ge=0)
    nested_subquery_depth: int = Field(ge=0)
    has_select_star: bool
    has_top_level_comma_in_from: bool
    has_cross_join: bool
    has_prewhere: bool
    range_days: int | None = None
    aggregation_functions: list[str] = Field(default_factory=list)
    group_by_columns: list[str] = Field(default_factory=list)

    @property
    def has_order_by_without_limit(self) -> bool:
        return self.has_order_by and not self.has_limit

    @property
    def has_missing_join_condition(self) -> bool:
        return self.join_count > self.join_condition_count


class SQLParser:
    def parse(self, sql: str) -> ParsedSQL:
        normalized = self._normalize(sql)
        from_clause = self._extract_clause(
            normalized,
            start_keyword="from",
            stop_keywords=("where", "group by", "order by", "having", "limit"),
        )
        aggregation_functions = re.findall(
            r"\b(sum|count|avg|min|max)\s*\(",
            normalized,
        )
        return ParsedSQL(
            original_sql=sql,
            normalized_sql=normalized,
            is_select=normalized.startswith("select") or normalized.startswith("with"),
            has_where=bool(re.search(r"\bwhere\b", normalized)),
            has_limit=bool(re.search(r"\blimit\s+\d+\b", normalized)),
            has_order_by=bool(re.search(r"\border\s+by\b", normalized)),
            has_group_by=bool(re.search(r"\bgroup\s+by\b", normalized)),
            join_count=len(re.findall(r"\bjoin\b", normalized)),
            join_condition_count=len(re.findall(r"\bon\b|\busing\s*\(", normalized)),
            distinct_count=len(re.findall(r"\bdistinct\b", normalized)),
            count_distinct_count=len(re.findall(r"\bcount\s*\(\s*distinct\b", normalized)),
            subquery_count=len(re.findall(r"\(\s*select\b", normalized)),
            nested_subquery_depth=self._nested_subquery_depth(normalized),
            has_select_star=bool(
                re.search(r"\bselect\s+\*", normalized)
                or re.search(r"\b[a-zA-Z_][\w]*\.\*", normalized)
            ),
            has_top_level_comma_in_from=self._has_top_level_comma(from_clause),
            has_cross_join=bool(re.search(r"\bcross\s+join\b", normalized)),
            has_prewhere=bool(re.search(r"\bprewhere\b", normalized)),
            range_days=self._extract_range_days(normalized),
            aggregation_functions=aggregation_functions,
            group_by_columns=self._extract_group_by_columns(normalized),
        )

    def _normalize(self, sql: str) -> str:
        without_comments = re.sub(r"--.*?$|/\*.*?\*/", " ", sql, flags=re.MULTILINE | re.DOTALL)
        return re.sub(r"\s+", " ", without_comments.strip()).lower()

    def _extract_clause(
        self,
        normalized_sql: str,
        *,
        start_keyword: str,
        stop_keywords: tuple[str, ...],
    ) -> str:
        start = re.search(rf"\b{re.escape(start_keyword)}\b", normalized_sql)
        if start is None:
            return ""
        tail = normalized_sql[start.end():]
        stop_indexes = [
            match.start()
            for keyword in stop_keywords
            if (match := re.search(rf"\b{re.escape(keyword)}\b", tail))
        ]
        end = min(stop_indexes) if stop_indexes else len(tail)
        return tail[:end]

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

    def _nested_subquery_depth(self, normalized_sql: str) -> int:
        depth = 0
        max_depth = 0
        for match in re.finditer(r"\(|\)|select", normalized_sql):
            token = match.group(0)
            if token == "(":
                depth += 1
            elif token == ")":
                depth = max(0, depth - 1)
            elif token == "select" and depth > 0:
                max_depth = max(max_depth, depth)
        return max_depth

    def _extract_range_days(self, normalized_sql: str) -> int | None:
        patterns = [
            r"interval\s+(\d+)\s+day",
            r"date_sub\s*\([^,]+,\s*(\d+)\s*\)",
            r"datediff\s*\([^)]*\)\s*<=\s*(\d+)",
            r">=\s*current_date\s*-\s*(\d+)",
        ]
        candidates: list[int] = []
        for pattern in patterns:
            candidates.extend(int(value) for value in re.findall(pattern, normalized_sql))
        return max(candidates) if candidates else None

    def _extract_group_by_columns(self, normalized_sql: str) -> list[str]:
        group_clause = self._extract_clause(
            normalized_sql,
            start_keyword="group by",
            stop_keywords=("order by", "having", "limit"),
        )
        if not group_clause:
            return []
        return [item.strip() for item in group_clause.split(",") if item.strip()]
