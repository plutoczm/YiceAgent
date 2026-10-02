from typing import Any

import pytest

from backend.app.application.agent.models import AgentIntent, AgentRequest
from backend.app.application.agent.state import AgentState
from backend.app.application.agent.validation import ResultValidation, ResultValidator
from backend.app.application.rag.models import RAGResponse
from backend.app.application.sql_review.models import RiskLevel, SQLReviewIssue, SQLReviewResult
from backend.app.application.text2sql.models import SQLEngine, SQLValidationResult, Text2SQLResult
from backend.app.application.warehouse_design.models import WarehouseDesignResult
from backend.app.domain.ports.llm_provider import LLMUsage


def base_state(**overrides: Any) -> AgentState:
    state: AgentState = {
        "request": AgentRequest(message="test"),
        "query": "test",
        "intent": AgentIntent.UNKNOWN,
        "confidence": 0.5,
        "intent_reason": "",
        "history": [],
        "routing_path": ["classify_intent"],
        "result": {},
        "final_response": "",
        "token_usage": LLMUsage(),
        "tool_usage": {},
        "errors": [],
        "validation": {},
    }
    state.update(overrides)
    return state


def make_text2sql_state(sql: str = "SELECT user_id FROM dwd_user_behavior_detail") -> AgentState:
    return base_state(
        request=AgentRequest(
            message="统计最近7天活跃用户",
            engine=SQLEngine.HIVE,
            schema_context="dwd_user_behavior_detail(user_id bigint, dt string)",
        ),
        query="统计最近7天活跃用户",
        intent=AgentIntent.TEXT2SQL,
        generated_sql=Text2SQLResult(
            sql=sql,
            explanation="explanation",
            optimization_suggestions=[],
            engine=SQLEngine.HIVE,
            confidence=0.9,
            validation=SQLValidationResult(is_valid=True),
        ),
    )


def test_text2sql_valid_sql_is_valid() -> None:
    validation = ResultValidator().validate(make_text2sql_state())

    assert isinstance(validation, ResultValidation)
    assert validation.is_valid is True
    assert any(check.code == "text2sql_valid" for check in validation.checks)


def test_text2sql_missing_result_is_invalid() -> None:
    validation = ResultValidator().validate(base_state(intent=AgentIntent.TEXT2SQL))

    assert validation.is_valid is False
    assert any(check.code == "text2sql_missing" for check in validation.checks)


def test_text2sql_invalid_sql_is_detected() -> None:
    state = make_text2sql_state(sql="DELETE FROM dwd_user_behavior_detail")
    validation = ResultValidator().validate(state)

    assert validation.is_valid is False
    check = next(c for c in validation.checks if c.code == "text2sql_valid")
    assert check.status.value == "error"


def test_sql_review_consistent_is_ok() -> None:
    review = SQLReviewResult(
        risk_level=RiskLevel.MEDIUM,
        score=88,
        issues=[
            SQLReviewIssue(
                code="select_star",
                title="SELECT *",
                description="select star",
                severity=RiskLevel.MEDIUM,
                suggestion="select columns",
                category="quality",
            )
        ],
        optimization_suggestions=[],
        engine=SQLEngine.HIVE,
    )
    state = base_state(
        request=AgentRequest(message="SELECT * FROM t", engine=SQLEngine.HIVE),
        query="SELECT * FROM t",
        intent=AgentIntent.SQL_REVIEW,
        review_result=review,
    )

    validation = ResultValidator().validate(state)

    assert validation.is_valid is True
    assert any(check.code == "sql_review_consistent" for check in validation.checks)


def test_sql_review_hallucinated_issue_is_warned() -> None:
    review = SQLReviewResult(
        risk_level=RiskLevel.LOW,
        score=95,
        issues=[
            SQLReviewIssue(
                code="made_up_issue",
                title="Made up",
                description="not real",
                severity=RiskLevel.LOW,
                suggestion="n/a",
                category="quality",
            )
        ],
        optimization_suggestions=[],
        engine=SQLEngine.HIVE,
    )
    state = base_state(
        request=AgentRequest(message="SELECT user_id FROM t WHERE dt='1'", engine=SQLEngine.HIVE),
        query="SELECT user_id FROM t WHERE dt='1'",
        intent=AgentIntent.SQL_REVIEW,
        review_result=review,
    )

    validation = ResultValidator().validate(state)

    assert any(check.code == "sql_review_inconsistent" for check in validation.checks)


def test_sql_review_missing_result_is_invalid() -> None:
    validation = ResultValidator().validate(base_state(intent=AgentIntent.SQL_REVIEW))

    assert validation.is_valid is False
    assert any(check.code == "sql_review_missing" for check in validation.checks)


def test_warehouse_design_complete_is_valid() -> None:
    state = base_state(
        request=AgentRequest(message="设计电商订单数仓"),
        intent=AgentIntent.WAREHOUSE_DESIGN,
        warehouse_design=WarehouseDesignResult.example("电商订单数仓"),
    )

    validation = ResultValidator().validate(state)

    assert validation.is_valid is True
    assert not any(c.code.startswith("warehouse_layer_") for c in validation.checks)


def test_warehouse_design_missing_layers_is_invalid() -> None:
    design = WarehouseDesignResult.example("电商订单数仓")
    design.ods = []
    state = base_state(
        request=AgentRequest(message="设计数仓"),
        intent=AgentIntent.WAREHOUSE_DESIGN,
        warehouse_design=design,
    )

    validation = ResultValidator().validate(state)

    assert validation.is_valid is False
    assert any(check.code == "warehouse_layer_ods_empty" for check in validation.checks)


def test_rag_with_answer_and_citations_is_valid() -> None:
    state = base_state(
        request=AgentRequest(message="什么是Spark AQE"),
        intent=AgentIntent.RAG,
        retrieved_context=RAGResponse(
            answer="Spark AQE dynamically adjusts query plans.",
            retrieved_chunks=[],
            citations=[
                {
                    "document_name": "spark.md",
                    "chunk_reference": "#chunk-0",
                    "similarity_score": 0.9,
                    "source_metadata": {},
                }
            ],
            metadata={"retrieved_count": 1},
        ),
    )

    validation = ResultValidator().validate(state)

    assert validation.is_valid is True


def test_rag_no_context_answer_is_invalid() -> None:
    state = base_state(
        request=AgentRequest(message="问题"),
        intent=AgentIntent.RAG,
        retrieved_context=RAGResponse(
            answer="I do not have enough retrieved context to answer this question reliably.",
            retrieved_chunks=[],
            citations=[],
            metadata={"retrieved_count": 0},
        ),
    )

    validation = ResultValidator().validate(state)

    assert validation.is_valid is False
    assert any(check.code == "rag_no_answer" for check in validation.checks)


def test_general_chat_has_no_structured_output_checks() -> None:
    state = base_state(
        request=AgentRequest(message="你好"),
        intent=AgentIntent.GENERAL_CHAT,
        general_answer="你好",
    )

    validation = ResultValidator().validate(state)

    assert validation.is_valid is True
    assert any(check.code == "no_structured_output" for check in validation.checks)


def test_malformed_state_is_defensive_and_never_raises() -> None:
    state = base_state(
        request=AgentRequest(message="hi"),
        intent=AgentIntent.WAREHOUSE_DESIGN,
        warehouse_design=None,
    )

    validation = ResultValidator().validate(state)

    assert validation.is_valid is False
    assert any(check.code == "warehouse_design_missing" for check in validation.checks)
