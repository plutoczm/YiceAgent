"""Spider 行 -> ShareGPT 指令记录。

默认目标引擎 spark_sql，并按需为 hive/mysql 复制受限子集（应用方言重写）。
训练用 Spider train 库，评测用 dev 库，天然无泄漏。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.app.application.text2sql.models import SQLEngine

from training.dataset_builder.common.records import ShareGPTRecord, build_record
from training.dataset_builder.common.render_prompt import (
    text2sql_system_prompt,
    text2sql_user_prompt,
)
from training.dataset_builder.spider.schema_from_spider import (
    build_schema_from_string,
    render_schema_for_prompt,
)
from training.dataset_builder.spider.sql_dialect_map import rewrite_sql


DEFAULT_ENGINES = (SQLEngine.SPARK_SQL,)
EXTRA_ENGINES = (SQLEngine.HIVE, SQLEngine.MYSQL)


def is_select_sql(sql: str) -> bool:
    stripped = sql.strip().lower()
    return stripped.startswith("select") or stripped.startswith("with")


def build_text2sql_records(
    rows: list[dict[str, Any]],
    tables_by_db: dict[str, dict] | None = None,
    *,
    max_per_db: int = 40,
    engines: tuple[SQLEngine, ...] = DEFAULT_ENGINES,
) -> list[ShareGPTRecord]:
    records: list[ShareGPTRecord] = []
    per_db: dict[str, int] = {}
    for row in rows:
        db_id = row.get("db_id") or "unknown"
        question = (row.get("question") or "").strip()
        sql = (row.get("query") or row.get("sql") or "").strip()
        if not question or not sql or not is_select_sql(sql):
            continue
        if per_db.get(db_id, 0) >= max_per_db:
            continue
        schema = _row_schema(row, tables_by_db, db_id)
        if not schema.tables:
            continue
        for engine in engines:
            gold_sql = rewrite_sql(sql, engine)
            answer = json.dumps(
                {
                    "sql": gold_sql,
                    "explanation": "Executed in SQLite and converted to "
                    f"{engine.value} dialect for this schema.",
                    "optimization_suggestions": [],
                    "confidence": 1.0,
                },
                ensure_ascii=False,
            )
            human = text2sql_user_prompt(
                question=question,
                engine=engine,
                schema=schema,
            )
            records.append(
                build_record(
                    system=text2sql_system_prompt(),
                    human=human,
                    gpt=answer,
                    metadata={
                        "capability": "text2sql",
                        "engine": engine.value,
                        "source": "spider",
                        "db_id": db_id,
                    },
                )
            )
        per_db[db_id] = per_db.get(db_id, 0) + 1
    return records


def build_text2sql_cases(
    rows: list[dict[str, Any]],
    tables_by_db: dict[str, dict] | None = None,
    *,
    max_cases: int = 200,
) -> list[dict[str, Any]]:
    """从 Spider dev 库构造评测用例（执行无关打分用）。"""
    cases: list[dict[str, Any]] = []
    for row in rows:
        db_id = row.get("db_id") or "unknown"
        question = (row.get("question") or "").strip()
        sql = (row.get("query") or row.get("sql") or "").strip()
        if not question or not sql or not is_select_sql(sql):
            continue
        schema = _row_schema(row, tables_by_db, db_id)
        if not schema.tables:
            continue
        engine = SQLEngine.SPARK_SQL
        cases.append(
            {
                "question": question,
                "prompt": text2sql_user_prompt(
                    question=question,
                    engine=engine,
                    schema=schema,
                ),
                "system": text2sql_system_prompt(),
                "gold_sql": rewrite_sql(sql, engine),
                "schema_context": schema.render_for_prompt(),
                "engine": engine.value,
                "db_id": db_id,
            }
        )
        if len(cases) >= max_cases:
            break
    return cases


def _row_schema(row: dict[str, Any], tables_by_db: dict[str, dict] | None, db_id: str):
    """按优先级取 schema：行内 schema_string（HF）> tables_by_db（官方 zip）。"""
    if row.get("schema_string"):
        return build_schema_from_string(row["schema_string"])
    db_meta = (tables_by_db or {}).get(db_id) or row.get("tables") or {}
    return render_schema_for_prompt(db_meta, db_id)


def load_tables_by_db(tables_path: Path | None, rows: list[dict]) -> dict[str, dict]:
    """优先从独立 tables.json 读取，否则从行内 tables 字段收集。"""
    tables: dict[str, dict] = {}
    if tables_path is not None and tables_path.is_file():
        entries = json.loads(tables_path.read_text(encoding="utf-8"))
        for entry in entries:
            tables[entry.get("db_id")] = entry
    for row in rows:
        if isinstance(row.get("tables"), dict):
            tables[row.get("db_id")] = row["tables"]
    return tables
