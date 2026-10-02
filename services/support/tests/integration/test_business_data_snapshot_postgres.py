"""Real PostgreSQL acceptance test for the exporter read-snapshot boundary."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.business_data.export_v1 import _read_snapshot
from app.store import Base, OrderRow

URL = os.getenv("S2B_POSTGRES_TEST_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="requires explicitly isolated PostgreSQL operation DB"),
]
TENANT = "TENANT-SNAPSHOT"


@pytest.fixture
def engines():
    url = make_url(URL)
    assert url.get_backend_name() == "postgresql"
    assert url.database in {"supportops_s2a", "supportops_s2b"}

    root = sa.create_engine(url)
    schema = "delivery_" + uuid4().hex
    with root.begin() as connection:
        connection.execute(sa.schema.CreateSchema(schema))
    scoped = url.update_query_dict({"options": "-csearch_path=" + schema})
    reader = sa.create_engine(scoped)
    writer = sa.create_engine(scoped)
    Base.metadata.create_all(reader)
    try:
        yield reader, writer
    finally:
        reader.dispose()
        writer.dispose()
        with root.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
        root.dispose()


def test_export_snapshot_is_repeatable_read_and_read_only(engines) -> None:
    reader_engine, writer_engine = engines
    with writer_engine.begin() as connection:
        connection.execute(
            OrderRow.__table__.insert().values(
                tenant_id=TENANT,
                order_id="ORD-SNAPSHOT-1",
                customer_id="CUST-001",
                status="delivered",
                amount=1.0,
                refundable_amount=1.0,
                refund_status="none",
                return_status="none",
            )
        )

    with _read_snapshot(reader_engine) as (reader, consistency):
        before = reader.scalar(sa.select(sa.func.count()).select_from(OrderRow))
        with writer_engine.begin() as writer:
            writer.execute(
                OrderRow.__table__.insert().values(
                    tenant_id=TENANT,
                    order_id="ORD-SNAPSHOT-2",
                    customer_id="CUST-001",
                    status="delivered",
                    amount=2.0,
                    refundable_amount=2.0,
                    refund_status="none",
                    return_status="none",
                )
            )
        after = reader.scalar(sa.select(sa.func.count()).select_from(OrderRow))
        assert reader.scalar(sa.text("SHOW transaction_read_only")) == "on"

    assert consistency == "postgresql_repeatable_read_read_only"
    assert before == after == 1
