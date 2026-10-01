import json

from tests.evaluation.business_analytics_evaluator import (
    CASES_PATH,
    load_cases,
    run_business_analytics_evaluation,
)


def test_versioned_business_analytics_dataset_has_representative_size() -> None:
    cases = load_cases()
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    assert payload["version"] == "1.0.0"
    assert len(cases) == 50
    assert {case["layer"] for case in cases} == {
        "routing",
        "authorization",
        "sql_scope",
        "execution",
        "api_auth",
    }


def test_deterministic_enterprise_evaluation_gate_passes_offline() -> None:
    report = run_business_analytics_evaluation()

    assert report["gate_passed"], json.dumps(
        {"metrics": report["metrics"], "errors": report["gate_errors"]},
        sort_keys=True,
    )
    assert report["metrics"]["tenant_isolation_violations"] == 0
    assert report["metrics"]["unsafe_query_accepted_count"] == 0
