def test_warehouse_design_generates_ddl_and_metrics(app_client) -> None:
    response = app_client.post(
        "/api/v1/warehouse-design",
        json={"requirement": "设计电商订单分析数仓", "use_rag": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ods"]
    assert payload["dwd"]
    assert payload["dws"]
    assert payload["ads"]
    assert any("CREATE TABLE" in item["sql"] for item in payload["ddl"])
    assert {"DAU", "WAU", "MAU", "GMV"}.issubset({item["name"] for item in payload["metrics"]})
