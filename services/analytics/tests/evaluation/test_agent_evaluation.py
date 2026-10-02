from backend.app.application.agent.models import AgentIntent
from backend.app.application.evaluation import AgentEvaluation, EvaluationCase


def test_agent_evaluation_calculates_task_specific_metrics() -> None:
    result = AgentEvaluation.from_cases(
        [
            EvaluationCase(
                expected_intent=AgentIntent.RAG,
                actual_intent=AgentIntent.RAG,
                expected_document_ids={"spark"},
                retrieved_document_ids=["other", "spark"],
            ),
            EvaluationCase(
                expected_intent=AgentIntent.TEXT2SQL,
                actual_intent=AgentIntent.UNKNOWN,
                tool_succeeded=False,
            ),
        ]
    )

    assert result.case_count == 2
    assert result.intent_accuracy == 0.5
    assert result.retrieval_precision_at_k == 0.5
    assert result.retrieval_mrr == 0.5
    assert result.tool_success_rate == 0.5
