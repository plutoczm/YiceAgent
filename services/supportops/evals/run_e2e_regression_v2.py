from __future__ import annotations

import json

from evals.e2e_holdout_v2 import build_e2e_holdout_v2_scenarios
from evals.e2e_sources import component_benchmark_texts, regression_v1_texts
from evals.e2e_suite import enforce_gates, evaluate_scenarios


def main() -> None:
    scenarios = build_e2e_holdout_v2_scenarios()
    metrics = evaluate_scenarios(
        scenarios,
        benchmark_version="supportops-e2e-regression-v2",
        scope=(
            "observed v2 E2E set; frozen as regression after its first run exposed "
            "account-security retrieval and prompt-injection detection defects"
        ),
        prohibited_texts=[*component_benchmark_texts(), *regression_v1_texts()],
    )
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    enforce_gates(metrics)


if __name__ == "__main__":
    main()
