from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from backend.app.application.sql_review.models import (
    RiskLevel,
    SQLReviewIssue,
    SQLReviewResult,
)
from backend.app.application.sql_review.rules import SQLReviewRuleEngine
from backend.app.application.sql_review.sql_parser import SQLParser
from backend.app.application.sql_review.sql_review_service import SQLReviewService
from backend.app.application.text2sql.models import SQLEngine, SQLValidationResult, Text2SQLResult
from backend.app.domain.ports.llm_provider import LLMMessage, LLMResponse, LLMUsage
from backend.app.main import create_app
from backend.app.presentation.api.dependencies.providers import get_sql_review_service


pytestmark = pytest.mark.anyio


class FakeLLMProvider:
    def __init__(self) -> None:
        self.messages: list[LLMMessage] = []

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.messages = list(messages)
        return LLMResponse(
            provider="fake",
            model="fake-review",
            content=(
                "The SQL risks come from scanning too much data and creating expensive "
                "shuffle work. Adding filters and join conditions reduces business latency."
            ),
            usage=LLMUsage(prompt_tokens=50, completion_tokens=30, total_tokens=80),
        )

    def token_count(self, text_or_messages):
        return 0

    def provider_name(self) -> str:
        return "fake"


class FakeText2SQLService:
    async def generate(self, **kwargs) -> Text2SQLResult:
        return Text2SQLResult(
            sql="SELECT * FROM dwd_order_detail",
            explanation="Generated SQL.",
            optimization_suggestions=["Add partition filter."],
            engine=SQLEngine.HIVE,
            confidence=0.8,
            validation=SQLValidationResult(is_valid=True),
        )


class FakeSQLReviewService:
    async def review(self, **kwargs) -> SQLReviewResult:
        return SQLReviewResult(
            risk_level=RiskLevel.HIGH,
            score=42,
            issues=[
                SQLReviewIssue(
                    code="select_star",
                    title="SELECT * detected",
                    description="Avoid SELECT *.",
                    severity=RiskLevel.MEDIUM,
                    suggestion="Select required columns only.",
                )
            ],
            optimization_suggestions=["Select required columns only."],
            llm_explanation="Rule engine found risky scan patterns.",
        )


def test_sql_parser_extracts_features_without_confusing_function_commas() -> None:
    parser = SQLParser()

    parsed = parser.parse(
        "SELECT DATE(create_time) AS dt, SUM(amount) AS gmv "
        "FROM dwd_order_detail "
        "WHERE create_time >= DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY) "
        "GROUP BY DATE(create_time) ORDER BY dt"
    )

    assert parsed.is_select is True
    assert parsed.has_where is True
    assert parsed.has_top_level_comma_in_from is False
    assert parsed.has_order_by_without_limit is True
    assert parsed.range_days == 30


def test_rule_engine_detects_core_risks_and_scores_high_risk_sql() -> None:
    result = SQLReviewRuleEngine().review(
        "SELECT * FROM dwd_order_detail",
        engine=SQLEngine.HIVE,
    )

    assert result.risk_level is RiskLevel.HIGH
    assert result.score <= 60
    assert result.has_issue("select_star")
    assert result.has_issue("missing_where")
    assert result.has_issue("missing_partition_filter")
    assert any("partition" in suggestion.lower() for suggestion in result.optimization_suggestions)


def test_rule_engine_detects_join_distinct_shuffle_and_sort_patterns() -> None:
    result = SQLReviewRuleEngine().review(
        "SELECT COUNT(DISTINCT user_id), province "
        "FROM user_info u JOIN order_info o "
        "GROUP BY province ORDER BY province",
        engine=SQLEngine.SPARK_SQL,
    )

    assert result.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    assert result.has_issue("missing_join_condition")
    assert result.has_issue("count_distinct_hotspot")
    assert result.has_issue("order_by_without_limit")
    assert result.has_issue("potential_shuffle_explosion")
    assert any("AQE" in suggestion for suggestion in result.optimization_suggestions)


def test_rule_engine_detects_cartesian_nested_skew_repeated_aggregation() -> None:
    result = SQLReviewRuleEngine().review(
        "SELECT province, SUM(amount), SUM(amount) "
        "FROM user_info u, order_info o "
        "WHERE province IN (SELECT province FROM dim_region WHERE id IN (SELECT id FROM dim_hot)) "
        "GROUP BY province",
        engine=SQLEngine.SPARK_SQL,
    )

    assert result.has_issue("cartesian_join")
    assert result.has_issue("nested_subquery_complexity")
    assert result.has_issue("potential_data_skew")
    assert result.has_issue("repeated_aggregation")


def test_engine_specific_hive_spark_and_clickhouse_rules() -> None:
    hive = SQLReviewRuleEngine().review(
        "SELECT order_id FROM dwd_order_detail WHERE amount > 100",
        engine=SQLEngine.HIVE,
    )
    assert hive.has_issue("hive_partition_awareness")
    assert any("MapJoin" in suggestion for suggestion in hive.optimization_suggestions)

    spark = SQLReviewRuleEngine().review(
        "SELECT u.user_id, o.amount FROM user_info u JOIN order_info o ON u.user_id = o.user_id",
        engine=SQLEngine.SPARK_SQL,
    )
    assert spark.has_issue("spark_shuffle_reduction")
    assert any("Broadcast Join" in suggestion for suggestion in spark.optimization_suggestions)

    clickhouse = SQLReviewRuleEngine().review(
        "SELECT user_id FROM user_info WHERE register_time >= yesterday() ORDER BY register_time",
        engine=SQLEngine.CLICKHOUSE,
    )
    assert clickhouse.has_issue("clickhouse_prewhere")
    assert clickhouse.has_issue("clickhouse_order_by_optimization")


async def test_sql_review_service_uses_rule_engine_then_llm_explanation() -> None:
    llm = FakeLLMProvider()
    service = SQLReviewService(llm_provider=llm)

    result = await service.review(
        sql="SELECT * FROM dwd_order_detail",
        engine=SQLEngine.SPARK_SQL,
    )

    assert result.risk_level is RiskLevel.HIGH
    assert result.has_issue("select_star")
    assert "shuffle" in result.llm_explanation.lower()
    assert llm.messages
    prompt = "\n".join(message.content for message in llm.messages)
    assert "Rules are authoritative" in prompt
    assert "select_star" in prompt


async def test_review_generated_sql_runs_text2sql_then_review() -> None:
    service = SQLReviewService(
        llm_provider=FakeLLMProvider(),
        text2sql_service=FakeText2SQLService(),
    )

    result = await service.review_generated_sql(
        question="统计最近7天订单",
        engine=SQLEngine.HIVE,
        schema_context="dwd_order_detail(order_id bigint, amount decimal(18,2))",
    )

    assert result.generated_sql is not None
    assert result.generated_sql.sql == "SELECT * FROM dwd_order_detail"
    assert result.review.has_issue("select_star")
    assert result.review.risk_level is RiskLevel.HIGH


def test_sql_review_api_endpoint_returns_structured_response() -> None:
    app = create_app()
    app.dependency_overrides[get_sql_review_service] = lambda: FakeSQLReviewService()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/v1/sql-review",
        json={"sql": "SELECT * FROM dwd_order_detail", "engine": "spark"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "HIGH"
    assert payload["score"] == 42
    assert payload["issues"][0]["code"] == "select_star"
    assert payload["optimization_suggestions"]
    assert payload["llm_explanation"]
