"""Prepare/drop only a generated P3 test schema in the owned demo database."""

import json
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from app.demo.fixtures import import_fixture, load_fixture, validate_url
from app.store import Base

ROOT = Path(__file__).resolve().parents[3]


def validate_schema(engine, schema):
    if re.fullmatch(r"p3_test_[a-f0-9]{32}", schema) is None:
        raise ValueError("invalid P3 test schema action")
    with engine.connect() as connection:
        if (
            connection.execute(text("SELECT owner FROM public.workflowops_demo_owner")).scalar()
            != "workflowops-demo"
        ):
            raise ValueError("unowned database")


def drop_test_schema(engine, schema):
    validate_schema(engine, schema)
    with engine.begin() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name=:schema"),
            {"schema": schema},
        ).scalar()
        if exists is None:
            return  # Idempotent cleanup after a rolled-back/failed preparation.
        marker = connection.execute(
            text("SELECT to_regclass(:marker)"),
            {"marker": f"{schema}.workflowops_demo_owner"},
        ).scalar()
        if marker is None:
            raise ValueError("unowned test schema")
        owners = (
            connection.execute(
                text(f'SELECT owner FROM "{schema}".workflowops_demo_owner'),
            )
            .scalars()
            .all()
        )
        if owners != ["workflowops-demo"]:
            raise ValueError("unowned test schema")
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def create_test_schema(engine, schema, fixture_directory):
    validate_schema(engine, schema)
    scoped = None
    created = False
    try:
        # Publish the schema and its ownership proof atomically. Any later failure
        # can safely clean up without treating an unknown schema as ours.
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(
                text(
                    f'CREATE TABLE "{schema}".workflowops_demo_owner '
                    "(owner TEXT PRIMARY KEY, fixture_hash TEXT)",
                )
            )
            connection.execute(
                text(
                    f'INSERT INTO "{schema}".workflowops_demo_owner '
                    "VALUES ('workflowops-demo',NULL)",
                )
            )
            created = True
        scoped = create_engine(engine.url, connect_args={"options": f"-csearch_path={schema}"})
        Base.metadata.create_all(scoped)
        fixture, digest = load_fixture(fixture_directory)
        import_fixture(scoped, fixture, digest)
    except Exception:
        if scoped is not None:
            scoped.dispose()
        if created:
            drop_test_schema(engine, schema)
        raise
    finally:
        if scoped is not None:
            scoped.dispose()


def main():
    action, schema = sys.argv[1:]
    if action not in {"create", "drop"}:
        raise ValueError("invalid P3 test schema action")
    config = json.loads((ROOT / ".runtime/demo-db.json").read_text(encoding="utf-8"))
    validate_url(config["database_url"])
    engine = create_engine(config["database_url"])
    try:
        if action == "create":
            create_test_schema(engine, schema, ROOT / ".runtime/demo-fixture")
        else:
            drop_test_schema(engine, schema)
    finally:
        engine.dispose()
    print(f"P3 test schema {action}: owned isolated fixture")


if __name__ == "__main__":
    main()
