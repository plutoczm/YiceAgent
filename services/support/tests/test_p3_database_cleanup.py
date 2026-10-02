"""Real PG cleanup after partial P3 preparation, with foreign-schema guards."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from app.demo.fixtures import ROOT, validate_url
from scripts.p3_test_database import create_test_schema, drop_test_schema


@pytest.fixture
def owned_database():
    config = json.loads((ROOT / ".runtime/demo-db.json").read_text(encoding="utf-8"))
    validate_url(config["database_url"])
    engine = create_engine(config["database_url"])
    try:
        yield engine
    finally:
        engine.dispose()


def schema_exists(engine, schema):
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name=:schema"),
                {"schema": schema},
            ).scalar()
            is not None
        )


@pytest.mark.parametrize("failure", ["fixture", "metadata", "import"])
def test_failed_preparation_leaves_no_schema(owned_database, tmp_path, monkeypatch, failure):
    import scripts.p3_test_database as module

    schema = "p3_test_" + uuid4().hex
    fixture_path = ROOT / ".runtime/demo-fixture"
    before = None
    with owned_database.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM public.orders")).scalar()
    if failure == "fixture":
        for name in ("fixture.json", "fixture-manifest.json"):
            (tmp_path / name).write_bytes((fixture_path / name).read_bytes())
        (tmp_path / "fixture.json").write_bytes((tmp_path / "fixture.json").read_bytes() + b" ")
        fixture_path = tmp_path
    else:

        def fail(*args, **kwargs):
            assert schema_exists(owned_database, schema)
            raise RuntimeError("injected preparation failure")

        if failure == "metadata":
            monkeypatch.setattr(module.Base.metadata, "create_all", fail)
        else:
            monkeypatch.setattr(module, "import_fixture", fail)
    try:
        with pytest.raises((ValueError, RuntimeError)):
            create_test_schema(owned_database, schema, fixture_path)
        assert not schema_exists(owned_database, schema)
        # The outer runner also performs cleanup after a nonzero exit.
        drop_test_schema(owned_database, schema)
        assert not schema_exists(owned_database, schema)
        with owned_database.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM public.orders")).scalar() == before
    finally:
        drop_test_schema(owned_database, schema)


@pytest.mark.parametrize("marker", ["absent", "foreign"])
def test_cleanup_refuses_foreign_schema(owned_database, marker):
    schema = "p3_test_" + uuid4().hex
    with owned_database.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        if marker == "foreign":
            connection.execute(text(f'CREATE TABLE "{schema}".workflowops_demo_owner(owner TEXT)'))
            connection.execute(
                text(f"INSERT INTO \"{schema}\".workflowops_demo_owner VALUES ('foreign')")
            )
    try:
        with pytest.raises(ValueError, match="unowned"):
            drop_test_schema(owned_database, schema)
        assert schema_exists(owned_database, schema)
    finally:
        # Explicit fixture-owned teardown; the guarded implementation refused it.
        with owned_database.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
