def test_upload_index_retrieve_and_generate_answer(app_client) -> None:
    upload = app_client.post(
        "/api/v1/knowledge/documents",
        data={"collection_name": "knowledge_base", "domain": "spark", "tags": "spark,aqe"},
        files={
            "file": (
                "spark_aqe.txt",
                b"Spark AQE optimizes joins, shuffle partitions, and skew at runtime.",
                "text/plain",
            )
        },
    )

    assert upload.status_code == 201
    uploaded = upload.json()
    assert uploaded["filename"] == "spark_aqe.txt"
    assert uploaded["chunk_count"] == 1

    listed = app_client.get("/api/v1/knowledge/documents")
    assert listed.status_code == 200
    assert listed.json()["documents"][0]["document_id"] == uploaded["document_id"]

    query = app_client.post(
        "/api/v1/knowledge/query",
        json={"question": "What does Spark AQE optimize?", "collection_name": "knowledge_base"},
    )

    assert query.status_code == 200
    payload = query.json()
    assert "runtime statistics" in payload["answer"]
    assert payload["citations"][0]["document_name"] == "spark_aqe.txt"
    assert payload["metadata"]["retrieved_count"] == 1
