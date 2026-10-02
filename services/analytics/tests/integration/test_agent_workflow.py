from tests.integration.conftest import SCHEMA_CONTEXT


def test_agent_router_classifies_executes_tool_and_returns_response(app_client) -> None:
    response = app_client.post(
        "/api/v1/agent/chat",
        json={
            "message": "统计最近7天活跃用户并检查SQL",
            "engine": "hive",
            "schema_context": SCHEMA_CONTEXT,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "TEXT2SQL_SQL_REVIEW"
    assert payload["routing_path"] == [
        "classify_intent",
        "planner",
        "text2sql",
        "sql_review",
        "format_response",
    ]
    assert "generated_sql" in payload["result"]
    assert payload["result"]["review"]["risk_level"] == "LOW"
    assert payload["metadata"]["tool_usage"] == {"text2sql": 1, "sql_review": 1}


def test_agent_stream_returns_sse_metadata_token_result_and_done(app_client) -> None:
    with app_client.stream(
        "POST",
        "/api/v1/agent/chat/stream",
        json={"message": "设计电商订单数仓"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: metadata" in body
    assert "WAREHOUSE_DESIGN" in body
    assert "event: token" in body
    assert "event: result" in body
    assert "event: done" in body
