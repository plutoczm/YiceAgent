"""Read-only/repeatable-read proof in a WorkflowOps-owned disposable PG schema."""

import importlib
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.exc import DBAPIError

from app.business_data.export_v1 import _read_snapshot
from app.demo.fixtures import validate_url
from app.store import OrderRow

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/support/scripts"))
database = importlib.import_module("p3_test_database")


def test_postgres_snapshot_remains_consistent_and_rejects_writes():
    config = json.loads((ROOT / ".runtime/demo-db.json").read_text())
    validate_url(config["database_url"])
    root = create_engine(config["database_url"])
    schema = "p3_test_" + uuid4().hex
    scoped = None
    try:
        database.create_test_schema(root, schema, ROOT / ".runtime/demo-fixture")
        scoped = create_engine(root.url, connect_args={"options": f"-csearch_path={schema}"})
        query = select(OrderRow.amount).where(OrderRow.order_id == "ORD-1001")
        with _read_snapshot(scoped) as (reader, consistency):
            before = reader.scalar(query)
            with scoped.begin() as writer:
                writer.execute(
                    update(OrderRow)
                    .where(OrderRow.order_id == "ORD-1001")
                    .values(amount=before + 1)
                )
            assert reader.scalar(query) == before
            assert reader.scalar(text("SHOW transaction_read_only")) == "on"
            assert reader.scalar(text("SHOW transaction_isolation")) == "repeatable read"
            assert consistency == "postgresql_repeatable_read_read_only"
        with scoped.connect() as fresh:
            assert fresh.scalar(query) == before + 1
        with pytest.raises(DBAPIError), _read_snapshot(scoped) as (reader, _):
            reader.execute(update(OrderRow).where(OrderRow.order_id == "ORD-1001").values(amount=0))
    finally:
        if scoped is not None:
            scoped.dispose()
        database.drop_test_schema(root, schema)
        root.dispose()
