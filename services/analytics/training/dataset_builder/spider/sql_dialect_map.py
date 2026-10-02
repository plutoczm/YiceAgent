"""SQLite -> Spark/Hive/MySQL 方言转换（best-effort）。

Spider 的参考 SQL 是 SQLite 方言。转换并非穷尽，目标是消除最明显的方言差异，
评测采用"执行无关、组件感知"的打分，避免因微小方言差异过度惩罚。
"""

from __future__ import annotations

import re

from backend.app.application.text2sql.models import SQLEngine


def rewrite_sql(sql: str, engine: SQLEngine) -> str:
    result = sql
    result = re.sub(r"`", "", result)
    if engine is SQLEngine.SPARK_SQL or engine is SQLEngine.HIVE:
        result = result.replace("ILIKE", "LOWER LIKE")
        result = re.sub(r"\bLIMIT\s+\d+\s+OFFSET\s+\d+\b", "LIMIT 10000", result)
        result = re.sub(r"\bOFFSET\s+\d+\b", "", result)
        result = re.sub(r"\bdatetime\s*\(", "FROM_UNIXTIME(", result)
        result = re.sub(r"\bstrftime\s*\(", "DATE_FORMAT(", result)
        result = re.sub(r"\bdate\s*\(", "TO_DATE(", result)
        result = re.sub(r"\babs\s*\(", "ABS(", result)
    elif engine is SQLEngine.MYSQL:
        result = re.sub(r"\bLIMIT\s+\d+\s+OFFSET\s+\d+\b", r"LIMIT \g<1>", result)
    return result


def normalize_engine(engine_value: str) -> SQLEngine:
    normalized = engine_value.strip().lower().replace("-", "_")
    if normalized in ("spark", "spark_sql"):
        return SQLEngine.SPARK_SQL
    if normalized in ("hive",):
        return SQLEngine.HIVE
    if normalized in ("clickhouse",):
        return SQLEngine.CLICKHOUSE
    return SQLEngine.MYSQL
