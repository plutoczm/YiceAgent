import json
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from backend.app.application.rag.models import RAGResponse
from backend.app.application.sql_review.models import RiskLevel, SQLReviewResult
from backend.app.application.text2sql.models import SQLEngine, SQLValidationResult, Text2SQLResult
from backend.app.application.warehouse_design.models import (
    MetricDefinition,
    TableLayer,
    WarehouseDesignResult,
)
from backend.app.application.warehouse_design.prompt_builder import WarehouseDesignPromptBuilder
from backend.app.application.warehouse_design.design_service import WarehouseDesignService
from backend.app.domain.ports.llm_provider import LLMMessage, LLMResponse, LLMUsage
from backend.app.infrastructure.llm.exceptions import LLMProviderError
from backend.app.main import create_app
from backend.app.presentation.api.dependencies.providers import get_warehouse_design_service


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
            model="fake-warehouse",
            content=json.dumps(
                {
                    "source_tables": [
                        {
                            "name": "mysql_order",
                            "layer": "SOURCE",
                            "description": "Operational order source table",
                            "columns": [
                                {"name": "order_id", "data_type": "bigint", "description": "order id"},
                                {"name": "user_id", "data_type": "bigint", "description": "user id"},
                                {"name": "amount", "data_type": "decimal(18,2)", "description": "order amount"},
                            ],
                        }
                    ],
                    "metrics": [
                        {
                            "name": "GMV",
                            "definition": "Total paid order amount",
                            "calculation_logic": "SUM(pay_amount)",
                            "business_meaning": "Measures sales scale",
                        }
                    ],
                    "recommendations": ["Partition fact tables by dt."],
                }
            ),
            usage=LLMUsage(prompt_tokens=120, completion_tokens=160, total_tokens=280),
        )

    def token_count(self, text_or_messages):
        return 0

    def provider_name(self) -> str:
        return "fake"


class InvalidJSONLLMProvider(FakeLLMProvider):
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
            model="fake-warehouse",
            content="{bad json",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FailingLLMProvider(FakeLLMProvider):
    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.messages = list(messages)
        raise LLMProviderError("DeepSeek request failed", provider="deepseek", retryable=True)


class FakeRAGService:
    def __init__(self) -> None:
        self.called = False

    async def answer(self, question: str, **kwargs) -> RAGResponse:
        self.called = True
        return RAGResponse(
            answer="Requirement docs mention order lifecycle, payment, refund, and user province dimensions.",
            retrieved_chunks=[],
            citations=[],
            metadata={"retrieved_count": 1},
        )


class FailingRAGService:
    async def answer(self, question: str, **kwargs) -> RAGResponse:
        raise LLMProviderError("RAG answer generation failed", provider="deepseek", retryable=True)


class FakeText2SQLService:
    async def generate(self, **kwargs) -> Text2SQLResult:
        return Text2SQLResult(
            sql="SELECT dt, SUM(pay_amount) AS gmv FROM dws_order_day GROUP BY dt",
            explanation="GMV query.",
            optimization_suggestions=["Use dt partition pruning."],
            engine=SQLEngine.HIVE,
            confidence=0.9,
            validation=SQLValidationResult(is_valid=True),
        )


class FakeSQLReviewService:
    async def review(self, **kwargs) -> SQLReviewResult:
        return SQLReviewResult(
            risk_level=RiskLevel.LOW,
            score=95,
            issues=[],
            optimization_suggestions=["SQL is well partitioned."],
            llm_explanation="The metric SQL is efficient.",
        )


class FakeWarehouseDesignService:
    async def design(self, **kwargs) -> WarehouseDesignResult:
        return WarehouseDesignResult.example("设计电商订单分析数仓")


def test_prompt_builder_includes_layers_metrics_rag_and_guardrails() -> None:
    messages = WarehouseDesignPromptBuilder().build_messages(
        requirement="设计电商订单分析数仓",
        rag_context="order lifecycle and refund documents",
    )

    prompt = "\n".join(message.content for message in messages)

    assert "ODS" in prompt
    assert "DWD" in prompt
    assert "DWS" in prompt
    assert "ADS" in prompt
    assert "DIM" in prompt
    assert "Hive-compatible DDL" in prompt
    assert "Metric Definitions" in prompt
    assert "order lifecycle" in prompt


async def test_design_service_generates_complete_ecommerce_design_with_ddl_and_metrics() -> None:
    llm = FakeLLMProvider()
    service = WarehouseDesignService(
        llm_provider=llm,
        text2sql_service=FakeText2SQLService(),
        sql_review_service=FakeSQLReviewService(),
    )

    result = await service.design(requirement="设计电商订单分析数仓", use_rag=False)

    assert result.requirement == "设计电商订单分析数仓"
    assert any(table.layer is TableLayer.ODS for table in result.ods)
    assert any(table.layer is TableLayer.DWD for table in result.dwd)
    assert any(table.layer is TableLayer.DWS for table in result.dws)
    assert any(table.layer is TableLayer.ADS for table in result.ads)
    assert result.dim
    assert result.fact_tables
    assert any("CREATE TABLE dwd_order_detail" in ddl.sql for ddl in result.ddl)
    assert all("PARTITIONED BY (dt STRING)" in ddl.sql for ddl in result.ddl)
    assert all("STORED AS PARQUET" in ddl.sql for ddl in result.ddl)
    assert {"DAU", "WAU", "MAU", "GMV", "ARPU", "Retention Rate", "Conversion Rate"}.issubset(
        {metric.name for metric in result.metrics}
    )
    assert result.data_flow.dependency_graph
    assert any("分区策略" in recommendation for recommendation in result.recommendations)
    assert result.metadata["recommendation_language"] == "zh-CN"
    prompt = "\n".join(message.content for message in llm.messages)
    assert "设计电商订单分析数仓" in prompt
    assert "Simplified Chinese" in prompt


async def test_design_service_can_generate_english_recommendations() -> None:
    llm = FakeLLMProvider()
    service = WarehouseDesignService(llm_provider=llm)

    result = await service.design(
        requirement="设计电商订单分析数仓",
        recommendation_language="en",
    )

    assert any("Partition Strategy" in item for item in result.recommendations)
    assert result.metadata["recommendation_language"] == "en"
    prompt = "\n".join(message.content for message in llm.messages)
    assert "recommendations in English" in prompt


async def test_design_service_uses_rag_context_when_enabled() -> None:
    rag = FakeRAGService()
    llm = FakeLLMProvider()
    service = WarehouseDesignService(llm_provider=llm, rag_service=rag)

    result = await service.design(requirement="设计用户行为分析数仓", use_rag=True)

    assert rag.called is True
    assert "Requirement docs mention" in result.metadata["rag_context"]
    prompt = "\n".join(message.content for message in llm.messages)
    assert "user province dimensions" in prompt


async def test_design_service_falls_back_to_template_when_llm_returns_invalid_json() -> None:
    service = WarehouseDesignService(llm_provider=InvalidJSONLLMProvider())

    result = await service.design(requirement="设计电商订单分析数仓", use_rag=False)

    assert result.ods
    assert result.dwd
    assert result.dws
    assert result.ads
    assert result.metadata["llm_payload_used"] is False
    assert result.metadata["llm_parse_error"] == "LLM response must be valid JSON"


async def test_design_service_falls_back_to_template_when_llm_provider_fails() -> None:
    service = WarehouseDesignService(llm_provider=FailingLLMProvider())

    result = await service.design(requirement="设计电商订单分析数仓", use_rag=False)

    assert result.ods
    assert result.dwd
    assert result.dws
    assert result.ads
    assert result.metadata["llm_payload_used"] is False
    assert result.metadata["llm_error"] == "DeepSeek request failed"


async def test_design_service_falls_back_to_template_when_rag_fails() -> None:
    llm = FakeLLMProvider()
    service = WarehouseDesignService(llm_provider=llm, rag_service=FailingRAGService())

    result = await service.design(requirement="设计电商订单分析数仓", use_rag=True)

    assert result.ods
    assert result.dwd
    assert result.dws
    assert result.ads
    assert result.metadata["rag_used"] is True
    assert result.metadata["rag_context"] == ""
    assert result.metadata["rag_error"] == "RAG answer generation failed"


async def test_design_service_generates_domain_specific_metrics() -> None:
    service = WarehouseDesignService(llm_provider=FakeLLMProvider())

    user_behavior = await service.design(requirement="设计用户行为分析数仓")
    advertising = await service.design(requirement="设计广告投放分析数仓")

    assert {"DAU", "WAU", "MAU", "Retention Rate", "Conversion Rate"}.issubset(
        {metric.name for metric in user_behavior.metrics}
    )
    assert {"Impressions", "Clicks", "CTR", "CPC", "ROAS", "Conversion Rate"}.issubset(
        {metric.name for metric in advertising.metrics}
    )


def test_metric_definition_requires_business_fields() -> None:
    metric = MetricDefinition(
        name="GMV",
        definition="Total paid order amount",
        calculation_logic="SUM(pay_amount)",
        business_meaning="Measures sales scale",
    )

    assert metric.name == "GMV"
    assert "SUM" in metric.calculation_logic
    assert metric.business_meaning


def test_warehouse_design_api_endpoint_returns_structured_design() -> None:
    app = create_app()
    app.dependency_overrides[get_warehouse_design_service] = lambda: FakeWarehouseDesignService()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/v1/warehouse-design",
        json={"requirement": "设计电商订单分析数仓", "use_rag": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ods"]
    assert payload["dwd"]
    assert payload["dws"]
    assert payload["ads"]
    assert payload["ddl"]
    assert payload["metrics"]
    assert payload["recommendations"]
