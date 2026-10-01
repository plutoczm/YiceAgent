from __future__ import annotations

from evals.cases import build_cases
from evals.e2e_cases import build_e2e_holdout_scenarios
from evals.retrieval_cases import build_retrieval_cases


def component_benchmark_texts() -> list[str]:
    texts = [str(case["text"]) for case in build_cases() if case.get("text")]
    texts.extend(
        str(case["query"])
        for case in build_retrieval_cases()
        if case.get("query")
    )
    return texts


def regression_v1_texts() -> list[str]:
    return [scenario.message for scenario in build_e2e_holdout_scenarios()]
