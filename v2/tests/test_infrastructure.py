"""Real owned v2 infrastructure gates, separated from model quality tests."""

import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, inspect, select
from workflowops.config import Settings
from workflowops.database import check_database, initialize_database, make_engine, owner_table

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WORKFLOWOPS_V2_INTEGRATION") != "1",
        reason="explicit_owned_integration_gate_not_enabled",
    ),
]


def test_real_postgres_ownership_and_idempotent_initialize():
    engine = make_engine(Settings.load())
    try:
        assert check_database(engine)
        initialize_database(engine)
        initialize_database(engine)
        with engine.connect() as connection:
            assert connection.execute(select(owner_table.c.owner)).scalars().all() == [
                "workflowops-v2"
            ]
            tables = set(inspect(connection).get_table_names())
            if "business_version" in tables:
                from workflowops.business.models import Base

                assert tables == set(Base.metadata.tables) | {"schema_owner"}
            else:
                assert tables == {"schema_owner"}
            assert connection.exec_driver_sql("SHOW server_version").scalar().startswith("16.")
    finally:
        engine.dispose()


def test_unowned_dialect_rejected_before_any_schema_write():
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(ValueError, match="database_not_owned_v2"):
            initialize_database(engine)
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_real_qdrant_auth_and_vector_storage():
    settings = Settings.load()
    url = "http://127.0.0.1:6335"
    collection = "workflowops_v2_foundation_" + uuid4().hex
    with httpx.Client(base_url=url, timeout=15, trust_env=False) as anonymous:
        assert anonymous.get("/collections").status_code in {401, 403}
    with httpx.Client(
        base_url=url,
        headers={"api-key": settings.qdrant_key.get_secret_value()},
        timeout=15,
        trust_env=False,
    ) as client:
        assert client.get("/collections").status_code == 200
        created = client.put(
            "/collections/" + collection, json={"vectors": {"size": 4, "distance": "Cosine"}}
        )
        assert created.status_code == 200
        try:
            written = client.put(
                "/collections/" + collection + "/points?wait=true",
                json={
                    "points": [
                        {
                            "id": 1,
                            "vector": [1.0, 0.0, 0.0, 0.0],
                            "payload": {
                                "owner": "workflowops-v2",
                                "kind": "transport_smoke_not_embedding",
                            },
                        }
                    ]
                },
            )
            assert written.status_code == 200
            result = client.post(
                "/collections/" + collection + "/points/query",
                json={"query": [1.0, 0.0, 0.0, 0.0], "limit": 1, "with_payload": True},
            )
            assert result.status_code == 200
            point = result.json()["result"]["points"][0]
            assert point["id"] == 1 and point["payload"]["owner"] == "workflowops-v2"
        finally:
            # Delete only the fresh UUID test collection created by this invocation.
            assert client.delete("/collections/" + collection).status_code == 200
