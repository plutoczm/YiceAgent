"""SQL 审查指令生成：模板组合 x 扰动 x 规则引擎标注。

答案由确定性规则引擎 SQLReviewRuleEngine.review() 生成，保证标注权威可靠。
通过模板 x 表/列替换 x 违规植入 x 扰动组合出足够的样本量（目标 500）。
"""

from __future__ import annotations

import json

from backend.app.application.sql_review.rules import SQLReviewRuleEngine
from backend.app.application.text2sql.models import SQLEngine

from training.dataset_builder.common.records import ShareGPTRecord, build_record
from training.dataset_builder.common.render_prompt import (
    sql_review_system_prompt,
    sql_review_user_prompt,
)
from training.dataset_builder.sql_review.seeds import base_seeds


# 每个引擎的可替换表/列集合，用于扩增
ENGINE_TABLES: dict[SQLEngine, list[tuple[str, str, str]]] = {
    SQLEngine.HIVE: [
        ("dwd_order_detail", "order_id, amount", "dt = '2026-01-01'"),
        ("dwd_page_view", "user_id, page_url", "dt = '2026-01-01'"),
        ("dws_sale_daily", "city, gmv", "dt = '2026-01-01'"),
        ("dwd_user_behavior", "user_id, event_name", "dt = '2026-01-01'"),
        ("dwd_pay_detail", "pay_id, pay_amount", "dt = '2026-01-01'"),
        ("dws_user_retention", "user_id, retention_days", "dt = '2026-01-01'"),
        ("dwd_goods_flow", "goods_id, flow_cnt", "dt = '2026-01-01'"),
    ],
    SQLEngine.SPARK_SQL: [
        ("dwd_order_detail", "order_id, amount", "dt = '2026-01-01'"),
        ("dws_user_stat", "user_id, active_days", "dt = '2026-01-01'"),
        ("dwd_event_log", "user_id, event_type", "dt = '2026-01-01'"),
        ("fact_order", "order_id, paid_amount", "dt = '2026-01-01'"),
        ("dws_ad_daily", "campaign_id, cost", "dt = '2026-01-01'"),
        ("dwd_sku_flow", "sku_id, pv", "dt = '2026-01-01'"),
        ("dws_channel_stat", "channel_id, uv", "dt = '2026-01-01'"),
    ],
    SQLEngine.MYSQL: [
        ("orders", "order_id, total_amount", "created_at >= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)"),
        ("users", "user_id, name", "created_at >= '2026-01-01'"),
        ("order_items", "item_id, quantity", "created_at >= '2026-01-01'"),
        ("payments", "payment_id, amount", "paid_at >= '2026-01-01'"),
        ("products", "product_id, stock", "updated_at >= '2026-01-01'"),
        ("shops", "shop_id, gmv", "created_at >= '2026-01-01'"),
    ],
    SQLEngine.CLICKHOUSE: [
        ("dwd_sale_detail", "category, amount", "dt = '2026-01-01'"),
        ("dws_traffic_daily", "channel, pv", "dt = '2026-01-01'"),
        ("dwd_user_act", "user_id, action", "dt = '2026-01-01'"),
        ("dws_ad_hourly", "ad_id, cost", "dt = '2026-01-01'"),
        ("dwd_stock_flow", "sku_id, qty", "dt = '2026-01-01'"),
        ("dws_search_daily", "keyword, cnt", "dt = '2026-01-01'"),
    ],
}

# 违规植入模板：返回 (sql, 描述)
def _templates(table: str, cols: str, filter_clause: str) -> list[tuple[str, str]]:
    col_a, _ = cols.split(",", 1)
    col_a = col_a.strip()
    return [
        (f"SELECT {col_a}, SUM({cols.split(',')[1].strip()}) AS total FROM {table} {filter_clause} GROUP BY {col_a}",
         "clean aggregation"),
        (f"SELECT * FROM {table} {filter_clause}", "select star"),
        (f"SELECT {cols} FROM {table}", "full table scan / no filter"),
        (f"SELECT {col_a}, COUNT(DISTINCT {col_a}) FROM {table} {filter_clause} GROUP BY {col_a}",
         "count distinct hotspot"),
        (f"SELECT a.{col_a} FROM {table} a, {table} b", "cartesian product"),
        (f"SELECT {col_a} FROM (SELECT {col_a} FROM {table} {filter_clause}) sub", "wrapped subquery"),
        (f"SELECT {col_a}, total FROM (SELECT {col_a}, SUM({cols.split(',')[1].strip()}) AS total FROM {table} {filter_clause} GROUP BY {col_a}) t ORDER BY total",
         "order by without limit"),
    ]


def _build_pool() -> list[tuple[SQLEngine, str, str]]:
    pool: list[tuple[SQLEngine, str, str]] = []
    for engine, table_set in ENGINE_TABLES.items():
        for table, cols, filter_clause in table_set:
            for sql, description in _templates(table, cols, filter_clause):
                pool.append((engine, sql, description))
                # 二级扰动：包裹子查询 + 外部 SELECT *（扩大样本量）
                pool.append((engine, f"SELECT * FROM ({sql}) AS sub", f"{description} / wrapped"))
    return pool


def _extra_perturbations(engine: SQLEngine, sql: str) -> list[tuple[str, str]]:
    """对池内 SQL 再施加一组确定性扰动。"""
    variants: list[tuple[str, str]] = []
    lowered = sql.lower()
    if "where" in lowered:
        head = sql.split(" where ", 1)[0]
        variants.append((head, "no filter"))
    if "group by" in lowered and "order by" not in lowered:
        variants.append((f"{sql} ORDER BY 1", "order by without limit"))
    return variants


def build_sql_review_records(
    *,
    target: int = 500,
    rule_engine: SQLReviewRuleEngine | None = None,
) -> list[ShareGPTRecord]:
    engine_instance = rule_engine or SQLReviewRuleEngine()
    records: list[ShareGPTRecord] = []
    seen: set[str] = set()

    # 手工种子优先（覆盖可读性更好的样例）
    for seed in base_seeds():
        sql = seed.sql.strip()
        key = f"{seed.engine.value}:{sql}"
        if key in seen:
            continue
        seen.add(key)
        review = engine_instance.review(sql, engine=seed.engine)
        records.append(_record_for(sql, seed.engine, review))
        if len(records) >= target:
            return records

    # 组合模板扩增（含二级扰动）
    for engine, sql, _ in _build_pool():
        for variant_sql, _ in [(sql, "base"), *_extra_perturbations(engine, sql)]:
            key = f"{engine.value}:{variant_sql}"
            if key in seen:
                continue
            seen.add(key)
            review = engine_instance.review(variant_sql, engine=engine)
            records.append(_record_for(variant_sql, engine, review))
            if len(records) >= target:
                return records
    return records


def _record_for(sql: str, engine: SQLEngine, review) -> ShareGPTRecord:
    answer = json.dumps(
        {
            "risk_level": review.risk_level.value,
            "score": review.score,
            "issues": [
                {
                    "code": issue.code,
                    "title": issue.title,
                    "severity": issue.severity.value,
                    "category": issue.category,
                    "suggestion": issue.suggestion,
                }
                for issue in review.issues
            ],
            "optimization_suggestions": review.optimization_suggestions,
        },
        ensure_ascii=False,
    )
    return build_record(
        system=sql_review_system_prompt(),
        human=sql_review_user_prompt(sql=sql, engine=engine),
        gpt=answer,
        metadata={
            "capability": "sql_review",
            "engine": engine.value,
            "source": "rule_engine",
        },
    )
