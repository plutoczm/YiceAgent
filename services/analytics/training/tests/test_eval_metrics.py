"""评测指标测试：gold 应得满分，扰动应降分。"""

from backend.app.application.text2sql.models import SQLEngine

from training.eval.metrics.score import render_summary_markdown, summarize
from training.eval.metrics.text2sql_metrics import (
    aggregate_text2sql,
    extract_components,
    score_text2sql_case,
)
from training.eval.metrics.warehouse_metrics import aggregate_warehouse, score_warehouse_case


GOLD_SQL = (
    "SELECT user_id, COUNT(*) AS pv FROM dwd_page_view "
    "WHERE dt = '2026-01-01' GROUP BY user_id HAVING COUNT(*) > 1 ORDER BY pv DESC LIMIT 10"
)
SCHEMA = "dwd_page_view(user_id bigint, dt string, page string)"


def test_identical_text2sql_scores_perfect() -> None:
    result = score_text2sql_case(
        predicted_sql=GOLD_SQL,
        gold_sql=GOLD_SQL,
        schema_context=SCHEMA,
        engine=SQLEngine.HIVE,
    )

    assert result["valid_sql"] is True
    assert result["exact_match"] is True
    assert result["component_f1"] == 1.0


def test_missing_join_table_reduces_f1() -> None:
    predicted = (
        "SELECT user_id, COUNT(*) AS pv FROM other_table "
        "WHERE dt = '2026-01-01' GROUP BY user_id ORDER BY pv DESC LIMIT 10"
    )
    result = score_text2sql_case(
        predicted_sql=predicted,
        gold_sql=GOLD_SQL,
        schema_context=SCHEMA,
        engine=SQLEngine.HIVE,
    )

    assert result["exact_match"] is False
    assert result["component_f1"] < 1.0
    assert result["components"]["tables"] < 1.0


def test_invalid_sql_is_reported_invalid() -> None:
    result = score_text2sql_case(
        predicted_sql="DELETE FROM dwd_page_view",
        gold_sql=GOLD_SQL,
        schema_context=SCHEMA,
        engine=SQLEngine.HIVE,
    )

    assert result["valid_sql"] is False


def test_extract_components_detects_aggregates_and_limit() -> None:
    components = extract_components(GOLD_SQL)

    assert "count" in components["aggregates"]
    assert components["has_limit"] is True
    assert "dwd_page_view" in components["tables"]
    assert "user_id" in components["group_by"]


def test_warehouse_gold_design_scores_perfect() -> None:
    design = {
        "source_tables": [],
        "ods": [{"name": "ods_order", "columns": []}],
        "dwd": [{"name": "dwd_order_detail", "columns": []}],
        "dws": [{"name": "dws_order_daily", "columns": []}],
        "ads": [{"name": "ads_order_report", "columns": []}],
        "dim": [{"name": "dim_user", "columns": []}],
        "fact_tables": [{"name": "fact_order", "columns": []}],
        "relationships": [],
        "ddl": [{"sql": "CREATE TABLE ..."}],
        "metrics": [{"name": "GMV"}],
        "recommendations": [],
    }
    import json

    result = score_warehouse_case(predicted=json.dumps(design, ensure_ascii=False))

    assert result["json_valid"] is True
    assert result["score"] == 1.0
    assert result["criteria"]["table_name_pattern"] is True


def test_warehouse_invalid_json_scores_zero() -> None:
    result = score_warehouse_case(predicted="not json at all")

    assert result["json_valid"] is False
    assert result["score"] == 0.0


def test_warehouse_missing_layers_drops_score() -> None:
    result = score_warehouse_case(predicted='{"ods": [{"name": "ods_x"}], "ddl": [], "metrics": []}')

    assert result["json_valid"] is True
    assert result["score"] < 1.0
    assert result["criteria"]["layer_ads"] is False


def test_aggregates_return_zero_for_empty() -> None:
    assert aggregate_text2sql([])["cases"] == 0
    assert aggregate_warehouse([])["cases"] == 0


def test_summarize_and_markdown_compare_models() -> None:
    base = {"text2sql": aggregate_text2sql([{"valid_sql": True, "component_f1": 0.5, "exact_match": False}]),
            "warehouse_design": aggregate_warehouse([{"json_valid": True, "score": 0.6}])}
    ft = {"text2sql": aggregate_text2sql([{"valid_sql": True, "component_f1": 0.8, "exact_match": True}]),
          "warehouse_design": aggregate_warehouse([{"json_valid": True, "score": 0.9}])}

    report = summarize(
        text2sql_results=[{"valid_sql": True, "component_f1": 0.8, "exact_match": True}],
        warehouse_results=[{"json_valid": True, "score": 0.9}],
    )
    markdown = render_summary_markdown(base, ft)

    assert report["text2sql"]["component_f1"] == 0.8
    assert "基座模型" in markdown
    assert "微调模型" in markdown
    assert "+0.3000" in markdown or "+0.3" in markdown
