"""Text2SQL 执行无关、schema 感知的打分。

无需真实数据库执行：复用应用层 SQLValidator 判断结构/引擎合法性，再用正则
提取预测 SQL 与 gold SQL 的组件（SELECT 列、表引用、WHERE 列、GROUP BY、
HAVING、ORDER BY、LIMIT、聚合函数、子查询数）计算组件级 P/R/F1。
"""

from __future__ import annotations

import re
from typing import Any

from backend.app.application.text2sql.models import SQLEngine
from backend.app.application.text2sql.schema_service import SchemaService
from backend.app.application.text2sql.sql_validator import SQLValidator


SELECT_COLUMNS = re.compile(r"select\s+(.+?)\s+from", re.IGNORECASE | re.DOTALL)
TABLE_REFERENCES = re.compile(r"\b(?:from|join)\s+([a-z_][\w]*)", re.IGNORECASE)
WHERE_COLUMNS = re.compile(r"\bwhere\s+([a-z_][\w]*\.)?([a-z_][\w]*)", re.IGNORECASE)
GROUP_BY = re.compile(r"group\s+by\s+(.+?)(?:\s+(?:having|order\s+by|limit)\b|\Z)", re.IGNORECASE | re.DOTALL)
HAVING = re.compile(r"having\s+(.+)", re.IGNORECASE)
ORDER_BY = re.compile(r"order\s+by\s+(.+)", re.IGNORECASE)
LIMIT = re.compile(r"\blimit\s+\d+", re.IGNORECASE)
AGGREGATES = re.compile(r"\b(count|sum|avg|max|min|count_distinct)\s*\(", re.IGNORECASE)
SUBQUERIES = re.compile(r"\bselect\b", re.IGNORECASE)


def extract_components(sql: str) -> dict[str, Any]:
    lower = re.sub(r"\s+", " ", sql.strip().lower())
    return {
        "select_columns": _set_from_match(SELECT_COLUMNS.search(lower), 1),
        "tables": set(TABLE_REFERENCES.findall(lower)),
        "where_columns": {
            col for _, col in WHERE_COLUMNS.findall(lower) if col
        },
        "group_by": _set_from_match(GROUP_BY.search(lower), 1),
        "having": _set_from_match(HAVING.search(lower), 1),
        "order_by": _set_from_match(ORDER_BY.search(lower), 1),
        "has_limit": bool(LIMIT.search(lower)),
        "aggregates": set(AGGREGATES.findall(lower)),
        "subquery_count": len(SUBQUERIES.findall(lower)),
    }


def _set_from_match(match: Any, group: int) -> set[str]:
    if not match:
        return set()
    text = match.group(group)
    if not text:
        return set()
    return {token.strip(" `,;") for token in re.split(r"[,\s]+", text) if token.strip(" `,;")}


def f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def _component_f1(predicted: set[str], gold: set[str]) -> float:
    if not gold and not predicted:
        return 1.0
    if not gold:
        return 0.0
    true_positive = len(predicted & gold)
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / len(gold)
    return f1(precision, recall)


def score_text2sql_case(
    *,
    predicted_sql: str,
    gold_sql: str,
    schema_context: str | None,
    engine: SQLEngine,
) -> dict[str, Any]:
    validator = SQLValidator()
    schema_service = SchemaService()
    schema = schema_service.parse_schema_context(schema_context, database_name=None)
    validation = validator.validate(predicted_sql, schema=schema, engine=engine)
    is_valid = validation.is_valid and not any(
        issue.severity == "error" for issue in validation.issues
    )

    pred_components = extract_components(predicted_sql)
    gold_components = extract_components(gold_sql)

    component_scores: dict[str, float] = {}
    for key in ("select_columns", "tables", "where_columns", "group_by",
                "having", "order_by", "aggregates"):
        component_scores[key] = _component_f1(pred_components[key], gold_components[key])
    if gold_components["has_limit"]:
        component_scores["limit"] = 1.0 if pred_components["has_limit"] else 0.0
    else:
        component_scores["limit"] = 1.0
    component_scores["subquery"] = (
        1.0 if pred_components["subquery_count"] == gold_components["subquery_count"] else 0.0
    )

    values = list(component_scores.values())
    overall_f1 = round(sum(values) / len(values), 4) if values else 1.0
    exact_match = normalize_sql(predicted_sql) == normalize_sql(gold_sql)

    return {
        "valid_sql": is_valid,
        "component_f1": overall_f1,
        "exact_match": exact_match,
        "components": component_scores,
    }


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().lower()).strip()


def aggregate_text2sql(results: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(results)
    if count == 0:
        return {"cases": 0, "valid_sql_rate": 0.0, "component_f1": 0.0, "exact_match_rate": 0.0}
    valid_rate = sum(1 for r in results if r["valid_sql"]) / count
    avg_f1 = sum(r["component_f1"] for r in results) / count
    exact_rate = sum(1 for r in results if r["exact_match"]) / count
    return {
        "cases": count,
        "valid_sql_rate": round(valid_rate, 4),
        "component_f1": round(avg_f1, 4),
        "exact_match_rate": round(exact_rate, 4),
    }
