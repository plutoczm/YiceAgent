from tests.integration.conftest import SCHEMA_CONTEXT


def test_natural_language_to_sql_then_sql_review(app_client) -> None:
    generated = app_client.post(
        "/api/v1/text2sql",
        json={
            "question": "统计最近7天活跃用户",
            "engine": "hive",
            "schema_context": SCHEMA_CONTEXT,
        },
    )

    assert generated.status_code == 200
    sql_payload = generated.json()
    assert "COUNT(DISTINCT user_id)" in sql_payload["sql"]
    assert sql_payload["validation"]["is_valid"] is True

    reviewed = app_client.post(
        "/api/v1/sql-review",
        json={"sql": sql_payload["sql"], "engine": "hive"},
    )

    assert reviewed.status_code == 200
    review_payload = reviewed.json()
    assert review_payload["risk_level"] == "LOW"
    assert review_payload["score"] >= 90
    assert review_payload["optimization_suggestions"]
