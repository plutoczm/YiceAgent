from tests.integration.conftest import SCHEMA_CONTEXT


def test_frontend_api_client_calls_backend_api_and_receives_renderable_payloads(
    frontend_backend_client,
) -> None:
    health = frontend_backend_client.health()
    assert health["service"] == "DataPilot-AI"

    uploaded = frontend_backend_client.upload_document(
        file_name="spark_aqe.txt",
        file_bytes=b"Spark AQE optimizes runtime Spark SQL plans.",
        collection_name="knowledge_base",
        domain="spark",
        tags="spark,aqe",
    )
    assert uploaded["document_id"].startswith("doc-")

    documents = frontend_backend_client.list_documents()
    assert documents["documents"][0]["filename"] == "spark_aqe.txt"

    rag = frontend_backend_client.query_knowledge("What is Spark AQE?")
    assert rag["citations"][0]["document_name"] == "spark_aqe.txt"

    generated = frontend_backend_client.text2sql(
        "统计最近7天活跃用户",
        "hive",
        SCHEMA_CONTEXT,
    )
    assert generated["validation"]["is_valid"] is True

    review = frontend_backend_client.sql_review(generated["sql"], "hive")
    assert review["risk_level"] == "LOW"

    design = frontend_backend_client.warehouse_design("设计电商订单分析数仓")
    assert design["ddl"]

    agent = frontend_backend_client.agent_chat(
        "统计最近7天活跃用户并检查SQL",
        schema_context=SCHEMA_CONTEXT,
    )
    assert agent["intent"] == "TEXT2SQL_SQL_REVIEW"

    events = list(frontend_backend_client.stream_agent_chat("介绍一下你"))
    assert events[0]["event"] == "metadata"
    assert events[-1]["event"] == "done"
