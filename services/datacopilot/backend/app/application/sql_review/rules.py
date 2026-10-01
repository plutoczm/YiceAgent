from collections import Counter

from backend.app.application.sql_review.models import (
    RiskLevel,
    SQLReviewIssue,
    SQLReviewResult,
)
from backend.app.application.sql_review.sql_parser import ParsedSQL, SQLParser
from backend.app.application.text2sql.models import SQLEngine


SEVERITY_PENALTY = {
    RiskLevel.LOW: 3,
    RiskLevel.MEDIUM: 7,
    RiskLevel.HIGH: 12,
    RiskLevel.CRITICAL: 35,
}


class SQLReviewRuleEngine:
    def __init__(self, parser: SQLParser | None = None) -> None:
        self.parser = parser or SQLParser()

    def review(self, sql: str, *, engine: SQLEngine) -> SQLReviewResult:
        parsed = self.parser.parse(sql)
        issues = self._evaluate(parsed, engine)
        score = self._score(issues)
        risk_level = self._risk_level(score, issues)
        suggestions = self._deduplicate([issue.suggestion for issue in issues])
        return SQLReviewResult(
            risk_level=risk_level,
            score=score,
            issues=issues,
            optimization_suggestions=suggestions,
            engine=engine,
            metadata={
                "rule_engine": "sql_review_v1",
                "issue_count": len(issues),
                "join_count": parsed.join_count,
                "subquery_count": parsed.subquery_count,
            },
        )

    def _evaluate(self, parsed: ParsedSQL, engine: SQLEngine) -> list[SQLReviewIssue]:
        issues: list[SQLReviewIssue] = []
        if not parsed.is_select:
            issues.append(
                self._issue(
                    "invalid_sql_structure",
                    "Invalid SQL structure",
                    "SQL review currently expects analytical SELECT statements.",
                    RiskLevel.CRITICAL,
                    "Rewrite the statement as a SELECT query before review.",
                    "correctness",
                )
            )

        if parsed.has_select_star:
            issues.append(
                self._issue(
                    "select_star",
                    "SELECT * detected",
                    "SELECT * increases scan volume and makes schemas brittle.",
                    RiskLevel.MEDIUM,
                    "Select only required columns.",
                    "quality",
                )
            )

        if not parsed.has_where:
            issues.append(
                self._issue(
                    "missing_where",
                    "Missing WHERE filter",
                    "Queries without filters can trigger full table scans.",
                    RiskLevel.HIGH,
                    "Add selective filters, especially date or partition filters.",
                    "performance",
                )
            )
            issues.append(
                self._issue(
                    "full_table_scan",
                    "Potential full table scan",
                    "No filter predicates were found for scanned tables.",
                    RiskLevel.HIGH,
                    "Add WHERE predicates to reduce scanned data.",
                    "performance",
                )
            )

        if engine in {SQLEngine.HIVE, SQLEngine.SPARK_SQL} and not self._has_partition_like_filter(parsed):
            issues.append(
                self._issue(
                    "missing_partition_filter",
                    "Missing partition filter",
                    "Large warehouse tables usually require partition pruning for stable performance.",
                    RiskLevel.HIGH,
                    "Filter on partition columns such as dt, ds, date, create_date, or event_date.",
                    "performance",
                )
            )

        if parsed.range_days is not None and parsed.range_days >= 30:
            issues.append(
                self._issue(
                    "large_range_scan",
                    "Large range scan",
                    f"The query scans about {parsed.range_days} days of data.",
                    RiskLevel.MEDIUM,
                    "Reduce the time range or pre-aggregate historical data.",
                    "performance",
                )
            )

        if parsed.has_top_level_comma_in_from or parsed.has_cross_join:
            issues.append(
                self._issue(
                    "cartesian_join",
                    "Potential Cartesian join",
                    "Comma joins or CROSS JOIN can multiply row counts unexpectedly.",
                    RiskLevel.CRITICAL,
                    "Use explicit JOIN syntax with ON conditions.",
                    "correctness",
                )
            )

        if parsed.has_missing_join_condition:
            issues.append(
                self._issue(
                    "missing_join_condition",
                    "Missing join condition",
                    "At least one JOIN does not have an ON or USING condition.",
                    RiskLevel.CRITICAL,
                    "Add join keys for every JOIN.",
                    "correctness",
                )
            )

        if parsed.distinct_count >= 2:
            issues.append(
                self._issue(
                    "excessive_distinct",
                    "Excessive DISTINCT usage",
                    "Multiple DISTINCT operations can cause expensive deduplication.",
                    RiskLevel.MEDIUM,
                    "Deduplicate earlier or use grouped intermediate tables.",
                    "performance",
                )
            )

        if parsed.count_distinct_count > 0:
            issues.append(
                self._issue(
                    "count_distinct_hotspot",
                    "COUNT(DISTINCT) hotspot",
                    "COUNT(DISTINCT) often creates high memory pressure and shuffle.",
                    RiskLevel.HIGH,
                    "Consider approximate distinct, bitmap, or pre-aggregated metrics.",
                    "performance",
                )
            )

        if parsed.nested_subquery_depth >= 2 or parsed.subquery_count >= 2:
            issues.append(
                self._issue(
                    "nested_subquery_complexity",
                    "Nested subquery complexity",
                    "Deeply nested subqueries are hard to optimize and maintain.",
                    RiskLevel.MEDIUM,
                    "Break the SQL into CTEs or materialized intermediate datasets.",
                    "maintainability",
                )
            )

        repeated_aggs = [
            function
            for function, count in Counter(parsed.aggregation_functions).items()
            if count >= 2
        ]
        if repeated_aggs:
            issues.append(
                self._issue(
                    "repeated_aggregation",
                    "Repeated aggregation",
                    "The SQL repeats aggregation functions that may be computed once and reused.",
                    RiskLevel.LOW,
                    "Use a CTE or alias to avoid repeated aggregation work.",
                    "quality",
                )
            )

        if parsed.has_order_by_without_limit:
            issues.append(
                self._issue(
                    "order_by_without_limit",
                    "ORDER BY without LIMIT",
                    "Global sorting without LIMIT can be expensive on large datasets.",
                    RiskLevel.MEDIUM,
                    "Add LIMIT for top-N queries or sort after aggregation on smaller results.",
                    "performance",
                )
            )
            issues.append(
                self._issue(
                    "unnecessary_sort",
                    "Potential unnecessary sort",
                    "The ORDER BY may not be needed for downstream computation.",
                    RiskLevel.LOW,
                    "Remove ORDER BY unless output ordering is required.",
                    "quality",
                )
            )

        if any(column in {"province", "city", "country", "gender", "status"} for column in parsed.group_by_columns):
            issues.append(
                self._issue(
                    "potential_data_skew",
                    "Potential data skew",
                    "Low-cardinality grouping keys can create skewed reducers or shuffle partitions.",
                    RiskLevel.MEDIUM,
                    "Check key distribution and add salting or two-stage aggregation if needed.",
                    "performance",
                )
            )

        if engine in {SQLEngine.HIVE, SQLEngine.SPARK_SQL} and (
            parsed.join_count > 0 or parsed.has_group_by or parsed.count_distinct_count > 0
        ):
            issues.append(
                self._issue(
                    "potential_shuffle_explosion",
                    "Potential shuffle explosion",
                    "Joins, GROUP BY, and distinct aggregations can trigger large shuffles.",
                    RiskLevel.MEDIUM,
                    "Pre-filter data, pre-aggregate facts, and reduce shuffle keys.",
                    "performance",
                )
            )

        issues.extend(self._engine_specific_issues(parsed, engine))
        return self._deduplicate_issues(issues)

    def _engine_specific_issues(
        self,
        parsed: ParsedSQL,
        engine: SQLEngine,
    ) -> list[SQLReviewIssue]:
        if engine is SQLEngine.HIVE:
            return [
                self._issue(
                    "hive_partition_awareness",
                    "Hive partition awareness",
                    "Hive performance depends heavily on partition pruning and table layout.",
                    RiskLevel.MEDIUM,
                    "Use partition awareness, MapJoin recommendations, and bucketing for repeated joins.",
                    "hive",
                )
            ]
        if engine is SQLEngine.SPARK_SQL:
            return [
                self._issue(
                    "spark_shuffle_reduction",
                    "Spark shuffle reduction",
                    "Spark SQL may spend most time in shuffle-heavy joins or aggregations.",
                    RiskLevel.MEDIUM,
                    "Use AQE, Broadcast Join suggestions, partition pruning, and pre-aggregation.",
                    "spark",
                )
            ]
        if engine is SQLEngine.CLICKHOUSE:
            issues = [
                self._issue(
                    "clickhouse_order_by_optimization",
                    "ClickHouse ORDER BY optimization",
                    "ClickHouse benefits when filters align with ORDER BY and primary key definitions.",
                    RiskLevel.MEDIUM,
                    "Align predicates with ORDER BY, primary key, and partition key usage.",
                    "clickhouse",
                )
            ]
            if parsed.has_where and not parsed.has_prewhere:
                issues.append(
                    self._issue(
                        "clickhouse_prewhere",
                        "ClickHouse PREWHERE candidate",
                        "Highly selective filters can be evaluated earlier with PREWHERE.",
                        RiskLevel.LOW,
                        "Consider PREWHERE for selective ClickHouse predicates.",
                        "clickhouse",
                    )
                )
            return issues
        return []

    def _has_partition_like_filter(self, parsed: ParsedSQL) -> bool:
        sql = parsed.normalized_sql
        partition_terms = (" dt", ".dt", " ds", ".ds", "date", "create_time", "event_time")
        return parsed.has_where and any(term in sql for term in partition_terms)

    def _score(self, issues: list[SQLReviewIssue]) -> int:
        penalty = sum(SEVERITY_PENALTY[issue.severity] for issue in issues)
        return max(0, min(100, 100 - penalty))

    def _risk_level(self, score: int, issues: list[SQLReviewIssue]) -> RiskLevel:
        if any(issue.severity is RiskLevel.CRITICAL for issue in issues) or score < 40:
            return RiskLevel.CRITICAL
        if score < 65:
            return RiskLevel.HIGH
        if score < 85:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _issue(
        self,
        code: str,
        title: str,
        description: str,
        severity: RiskLevel,
        suggestion: str,
        category: str,
    ) -> SQLReviewIssue:
        return SQLReviewIssue(
            code=code,
            title=title,
            description=description,
            severity=severity,
            suggestion=suggestion,
            category=category,
        )

    def _deduplicate(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            normalized = value.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(value)
        return result

    def _deduplicate_issues(self, issues: list[SQLReviewIssue]) -> list[SQLReviewIssue]:
        seen: set[str] = set()
        result: list[SQLReviewIssue] = []
        for issue in issues:
            if issue.code in seen:
                continue
            seen.add(issue.code)
            result.append(issue)
        return result
