def test_sql_review_detects_risky_sql_through_backend_api(app_client) -> None:
    response = app_client.post(
        "/api/v1/sql-review",
        json={
            "sql": "SELECT * FROM dwd_order_detail WHERE dt >= '2026-06-01'",
            "engine": "spark",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "MEDIUM"
    assert payload["score"] == 76
    assert payload["issues"][0]["code"] == "select_star"
    assert "partition" in " ".join(payload["optimization_suggestions"]).lower()
