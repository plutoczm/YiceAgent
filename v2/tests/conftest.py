"""Disposable real v2 PostgreSQL schema; never reset the demonstration schema."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from workflowops.business.bootstrap import provision, seed
from workflowops.business.service import BusinessService
from workflowops.config import Settings
from workflowops.database import make_engine


@pytest.fixture
def business():
    if os.environ.get("WORKFLOWOPS_V2_INTEGRATION") != "1":
        pytest.skip("explicit_owned_integration_gate_not_enabled")
    schema = "v2_test_" + uuid4().hex
    control = make_engine(Settings.load())
    with control.begin() as connection:
        assert connection.exec_driver_sql(
            "SELECT owner FROM public.schema_owner"
        ).scalars().all() == ["workflowops-v2"]
        connection.execute(text('CREATE SCHEMA "' + schema + '"'))
    engine = make_engine(Settings.load(), schema)
    try:
        provision(engine, schema)
        seed(engine, schema)
        yield BusinessService(engine), schema
    finally:
        engine.dispose()
        with control.begin() as connection:
            # This exact UUID schema was created by this fixture, never provided by a caller.
            assert schema.startswith("v2_test_") and len(schema) == 40
            connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        control.dispose()
