"""SQL 审查种子片段（clean 与违规植入），供规则引擎自动标注。

每条种子附带目标引擎。生成器会对种子施加扰动以扩增样本量，并逐条去重。
"""

from __future__ import annotations

from backend.app.application.text2sql.models import SQLEngine


class SeedSQL:
    def __init__(self, sql: str, engine: SQLEngine, description: str = "") -> None:
        self.sql = sql
        self.engine = engine
        self.description = description


def base_seeds() -> list[SeedSQL]:
    return [
        # ---- clean ----
        SeedSQL(
            "SELECT user_id, COUNT(*) AS pv FROM dwd_page_view WHERE dt = '2026-01-01' GROUP BY user_id",
            SQLEngine.HIVE,
            "clean hive aggregation",
        ),
        SeedSQL(
            "SELECT order_id, amount FROM dwd_order_detail WHERE dt = '2026-01-01' LIMIT 100",
            SQLEngine.HIVE,
            "clean hive select",
        ),
        SeedSQL(
            "SELECT city, SUM(gmv) AS total FROM dwd_sale_detail WHERE dt >= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY) GROUP BY city ORDER BY total DESC LIMIT 10",
            SQLEngine.MYSQL,
            "clean mysql top-n",
        ),
        SeedSQL(
            "SELECT device, COUNT(DISTINCT user_id) AS dau FROM dws_dau WHERE dt = '2026-01-01' GROUP BY device",
            SQLEngine.SPARK_SQL,
            "clean spark aggregation",
        ),
        SeedSQL(
            "SELECT category, sum(amount) FROM dwd_sale_detail WHERE dt = '2026-01-01' GROUP BY category",
            SQLEngine.CLICKHOUSE,
            "clean clickhouse aggregation",
        ),
        # ---- violation: select * ----
        SeedSQL(
            "SELECT * FROM dwd_order_detail WHERE dt = '2026-01-01'",
            SQLEngine.HIVE,
            "select star",
        ),
        # ---- violation: missing where (full scan) ----
        SeedSQL(
            "SELECT user_id, amount FROM dwd_order_detail",
            SQLEngine.HIVE,
            "full table scan",
        ),
        # ---- violation: missing partition filter on hive ----
        SeedSQL(
            "SELECT user_id, COUNT(*) FROM dwd_page_view GROUP BY user_id",
            SQLEngine.HIVE,
            "missing partition filter",
        ),
        # ---- violation: cartesian join ----
        SeedSQL(
            "SELECT a.user_id, b.order_id FROM dwd_user a, dwd_order b",
            SQLEngine.SPARK_SQL,
            "cartesian product",
        ),
        # ---- violation: count distinct hotspot ----
        SeedSQL(
            "SELECT dt, COUNT(DISTINCT user_id) FROM dwd_page_view WHERE dt = '2026-01-01' GROUP BY dt",
            SQLEngine.SPARK_SQL,
            "count distinct hotspot",
        ),
        # ---- violation: nested subquery ----
        SeedSQL(
            "SELECT user_id FROM (SELECT user_id, ROW_NUMBER() OVER (PARTITION BY dt ORDER BY pv DESC) rn FROM (SELECT user_id, dt, COUNT(*) pv FROM dwd_page_view WHERE dt = '2026-01-01' GROUP BY user_id, dt) t) t2 WHERE rn = 1",
            SQLEngine.SPARK_SQL,
            "nested subquery",
        ),
        # ---- violation: order by without limit ----
        SeedSQL(
            "SELECT user_id, pv FROM dwd_user_stat ORDER BY pv DESC",
            SQLEngine.MYSQL,
            "order by without limit",
        ),
        # ---- violation: clickhouse missing prewhere ----
        SeedSQL(
            "SELECT category, sum(amount) FROM dwd_sale_detail WHERE amount > 100 GROUP BY category",
            SQLEngine.CLICKHOUSE,
            "clickhouse filter could use prewhere",
        ),
    ]


def seeds_by_engine() -> dict[SQLEngine, list[SeedSQL]]:
    grouped: dict[SQLEngine, list[SeedSQL]] = {}
    for seed in base_seeds():
        grouped.setdefault(seed.engine, []).append(seed)
    return grouped
