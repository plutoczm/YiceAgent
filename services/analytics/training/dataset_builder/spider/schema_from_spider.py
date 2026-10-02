"""从 Spider 表结构构建 DatabaseSchema（复用应用层模型）。

支持两种来源：
1. 官方 tables.json 结构（table_names_original / column_names_original / column_types）。
2. HuggingFace richardr1126/spider-schema 的字符串格式：
   "db_id : col1 (type) , col2 (type) | table2 : col1 (type) , ..."
"""

from __future__ import annotations

import re

from backend.app.application.text2sql.models import DatabaseSchema, SchemaColumn, TableSchema

from training.dataset_builder.common.records import ShareGPTRecord  # noqa: F401

_COLUMN_PATTERN = re.compile(r"^(.+?)\s*\((.+?)\)\s*$")


def build_schema_from_string(schema_string: str | None) -> DatabaseSchema:
    """解析 richardr1126/spider-schema 的字符串为 DatabaseSchema。"""
    if not schema_string:
        return DatabaseSchema(tables=[])
    tables: list[TableSchema] = []
    for group in schema_string.split("|"):
        parts = group.split(":", 1)
        if len(parts) != 2:
            continue
        table_name = parts[0].strip()
        if not table_name:
            continue
        columns: list[SchemaColumn] = []
        for raw_column in parts[1].split(","):
            column = raw_column.strip()
            if not column:
                continue
            match = _COLUMN_PATTERN.match(column)
            if match:
                columns.append(
                    SchemaColumn(name=match.group(1).strip(), data_type=match.group(2).strip())
                )
            else:
                columns.append(SchemaColumn(name=column, data_type="text"))
        if columns:
            tables.append(TableSchema(name=table_name, columns=columns))
    return DatabaseSchema(tables=tables)


def build_schema(db_meta: dict, db_id: str) -> DatabaseSchema:
    table_names = db_meta.get("table_names_original") or db_meta.get("table_names") or []
    columns = db_meta.get("column_names_original") or db_meta.get("column_names") or []
    types = db_meta.get("column_types") or []

    tables: list[TableSchema] = []
    for table_index, table_name in enumerate(table_names):
        table_columns: list[SchemaColumn] = []
        for pair, data_type in zip(columns, types):
            index = pair[0]
            if index != table_index:
                continue
            column_name = pair[1]
            if column_name == "*":
                continue
            table_columns.append(
                SchemaColumn(
                    name=column_name,
                    data_type=str(data_type or "text").lower(),
                    description=None,
                )
            )
        if table_columns:
            tables.append(
                TableSchema(name=table_name, columns=table_columns, database=None)
            )
    return DatabaseSchema(database_name=db_id, tables=tables)


def render_schema_for_prompt(db_meta: dict, db_id: str) -> DatabaseSchema:
    """构建可直接用于 prompt 渲染的 schema。"""
    return build_schema(db_meta, db_id)
