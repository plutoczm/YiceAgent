from __future__ import annotations

import json

from evals.e2e_cases import build_e2e_holdout_scenarios
from evals.e2e_sources import component_benchmark_texts
from evals.e2e_suite import enforce_gates, evaluate_scenarios


def main() -> None:
    metrics = evaluate_scenarios(
        build_e2e_holdout_scenarios(),
        benchmark_version="supportops-e2e-regression-v1",
        scope=(
            "observed first-run E2E set; frozen as regression after it exposed the "
            "shipping-address routing defect"
        ),
        prohibited_texts=component_benchmark_texts(),
    )
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    enforce_gates(metrics)


if __name__ == "__main__":
    main()
