from pydantic import BaseModel, Field

from backend.app.application.agent.models import AgentIntent


class EvaluationCase(BaseModel):
    expected_intent: AgentIntent
    actual_intent: AgentIntent
    expected_document_ids: set[str] = Field(default_factory=set)
    retrieved_document_ids: list[str] = Field(default_factory=list)
    tool_succeeded: bool = True


class AgentEvaluation(BaseModel):
    case_count: int
    intent_accuracy: float
    retrieval_precision_at_k: float
    retrieval_mrr: float
    tool_success_rate: float

    @classmethod
    def from_cases(cls, cases: list[EvaluationCase]) -> "AgentEvaluation":
        if not cases:
            return cls(
                case_count=0,
                intent_accuracy=0.0,
                retrieval_precision_at_k=0.0,
                retrieval_mrr=0.0,
                tool_success_rate=0.0,
            )
        intent_hits = sum(case.actual_intent == case.expected_intent for case in cases)
        precisions: list[float] = []
        reciprocal_ranks: list[float] = []
        for case in cases:
            if not case.expected_document_ids:
                continue
            retrieved = case.retrieved_document_ids
            hits = sum(item in case.expected_document_ids for item in retrieved)
            precisions.append(hits / max(len(retrieved), 1))
            first_rank = next(
                (
                    index
                    for index, item in enumerate(retrieved, start=1)
                    if item in case.expected_document_ids
                ),
                None,
            )
            reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)
        return cls(
            case_count=len(cases),
            intent_accuracy=intent_hits / len(cases),
            retrieval_precision_at_k=sum(precisions) / len(precisions) if precisions else 0.0,
            retrieval_mrr=(
                sum(reciprocal_ranks) / len(reciprocal_ranks)
                if reciprocal_ranks
                else 0.0
            ),
            tool_success_rate=sum(case.tool_succeeded for case in cases) / len(cases),
        )
