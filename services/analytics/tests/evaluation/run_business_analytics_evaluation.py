"""Run the deterministic, offline D5 enterprise analytics gate."""

import argparse
import json
from pathlib import Path

from tests.evaluation.business_analytics_evaluator import (
    run_business_analytics_evaluation,
    write_json_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optional path for the safe metrics and case-status report.",
    )
    args = parser.parse_args()
    report = run_business_analytics_evaluation()
    print(f"dataset_version: {report['dataset_version']}")
    print(f"total_cases: {report['total_cases']}")
    for name, value in report["metrics"].items():
        if isinstance(value, float):
            print(f"{name}: {value:.2%}")
        else:
            print(f"{name}: {value}")
    print(f"gate: {'PASS' if report['gate_passed'] else 'FAIL'}")
    if report["gate_errors"]:
        print("gate_errors: " + ", ".join(report["gate_errors"]))
    failed = [case for case in report["case_results"] if case["status"] != "pass"]
    if failed:
        print("failed_cases: " + json.dumps(failed, sort_keys=True))
    if args.json_report:
        write_json_report(report, args.json_report)
    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
