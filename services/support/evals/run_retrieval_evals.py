from __future__ import annotations

import json

from app.knowledge import KnowledgeService
from evals.retrieval_cases import build_retrieval_cases


def main() -> None:
    service = KnowledgeService()
    cases = build_retrieval_cases()

    supported = [case for case in cases if bool(case["supported"])]
    unsupported = [case for case in cases if not bool(case["supported"])]

    recall_at_1 = 0
    recall_at_3 = 0
    reciprocal_rank_sum = 0.0
    answer_citation_presence = 0
    unsupported_abstentions = 0
    failures: list[dict[str, object]] = []

    for case in supported:
        query = str(case["query"])
        relevant = {str(value) for value in case["relevant_ids"]}
        hits = service.retriever.search(query, limit=3, prefetch=10)
        ranked_ids = [hit.document.document_id for hit in hits]

        if ranked_ids and ranked_ids[0] in relevant:
            recall_at_1 += 1
        else:
            failures.append(
                {
                    "id": case["id"],
                    "surface": "retrieval@1",
                    "expected": sorted(relevant),
                    "actual": ranked_ids[:1],
                }
            )

        rank = next(
            (
                index
                for index, doc_id in enumerate(ranked_ids, 1)
                if doc_id in relevant
            ),
            None,
        )
        if rank is not None and rank <= 3:
            recall_at_3 += 1
            reciprocal_rank_sum += 1.0 / rank
        else:
            failures.append(
                {
                    "id": case["id"],
                    "surface": "retrieval@3",
                    "expected": sorted(relevant),
                    "actual": ranked_ids,
                }
            )

        citations = service.search(query, limit=3)
        answer = service.answer(query, citations)
        if citations and f"[{citations[0].document_id}]" in answer:
            answer_citation_presence += 1
        else:
            failures.append(
                {
                    "id": case["id"],
                    "surface": "answer_citation",
                    "citations": [citation.document_id for citation in citations],
                }
            )

    for case in unsupported:
        citations = service.search(str(case["query"]), limit=3)
        if not citations:
            unsupported_abstentions += 1
        else:
            failures.append(
                {
                    "id": case["id"],
                    "surface": "unsupported_abstention",
                    "actual": [citation.document_id for citation in citations],
                    "scores": [citation.score for citation in citations],
                }
            )

    metrics = {
        "benchmark_version": "supportops-retrieval-v1",
        "cases": len(cases),
        "supported_cases": len(supported),
        "unsupported_cases": len(unsupported),
        "recall_at_1": _ratio(recall_at_1, len(supported)),
        "recall_at_3": _ratio(recall_at_3, len(supported)),
        "mrr_at_3": round(reciprocal_rank_sum / max(len(supported), 1), 4),
        "answer_citation_presence": _ratio(answer_citation_presence, len(supported)),
        "unsupported_abstention_recall": _ratio(
            unsupported_abstentions, len(unsupported)
        ),
        "failures": failures,
    }
    print(json.dumps(metrics, indent=2, ensure_ascii=False))

    if len(cases) < 60:
        raise SystemExit("retrieval benchmark must contain at least 60 cases")
    if metrics["recall_at_1"] < 0.94:
        raise SystemExit("retrieval recall@1 below 0.94")
    if metrics["recall_at_3"] < 1.0:
        raise SystemExit("retrieval recall@3 below 1.0")
    if metrics["mrr_at_3"] < 0.97:
        raise SystemExit("retrieval MRR@3 below 0.97")
    if metrics["answer_citation_presence"] < 1.0:
        raise SystemExit("grounded answer citation contract regressed")
    if metrics["unsupported_abstention_recall"] < 0.9:
        raise SystemExit("unsupported-query abstention below 0.90")


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


if __name__ == "__main__":
    main()
