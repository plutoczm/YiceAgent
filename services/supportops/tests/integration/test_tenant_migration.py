"""Real PostgreSQL tenant migration contract, only against an explicit isolated test DB."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

URL = os.getenv("S2A_POSTGRES_TEST_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="requires explicitly isolated PostgreSQL migration DB"),
]
TABLES = (
    "orders",
    "tickets",
    "ticket_events",
    "ticket_messages",
    "pending_actions",
    "action_receipts",
    "audit_events",
)
OLD_HEAD = "20260825_0003"
NEW_HEAD = "20260917_0005"


def test_postgresql_tenant_upgrade_backfill_downgrade_reupgrade(monkeypatch):
    url = make_url(URL)
    assert url.get_backend_name() == "postgresql"
    assert url.database == "supportops_s2a", "only the dedicated S2a test database is allowed"
    root = sa.create_engine(url)
    schema = "s2a_" + uuid4().hex
    with root.begin() as connection:
        connection.execute(sa.schema.CreateSchema(schema))
    scoped = url.update_query_dict({"options": "-csearch_path=" + schema})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = sa.create_engine(scoped)
    cfg = Config("alembic.ini")
    cfg.cmd_opts = SimpleNamespace(x=[])
    try:
        command.upgrade(cfg, OLD_HEAD)
        metadata = sa.MetaData()
        metadata.reflect(engine)
        now = datetime.now(UTC)
        rows = {
            "orders": dict(
                order_id="ORD-LEGACY",
                customer_id="CUST-001",
                status="delivered",
                amount=199,
                refundable_amount=199,
                refund_status="requested",
                return_status="none",
            ),
            "tickets": dict(
                ticket_id="TKT-LEGACY",
                customer_id="CUST-001",
                conversation_id="CONV",
                reason="synthetic",
                status="open",
                priority="normal",
                assignee_id=None,
                sla_due_at=now,
                created_at=now,
                updated_at=now,
            ),
            "ticket_events": dict(
                event_id="TEV-LEGACY",
                ticket_id="TKT-LEGACY",
                actor_id="agent",
                from_status="open",
                to_status="assigned",
                note=None,
                created_at=now,
            ),
            "ticket_messages": dict(
                message_id="MSG-LEGACY",
                ticket_id="TKT-LEGACY",
                conversation_id="CONV",
                customer_id="CUST-001",
                sender_role="customer",
                sender_id="user",
                body="synthetic",
                context={},
                created_at=now,
            ),
            "pending_actions": dict(
                action_id="ACT-LEGACY",
                conversation_id="CONV",
                customer_id="CUST-001",
                kind="refund",
                payload={"order_id": "ORD-LEGACY", "amount": 199},
                status="pending",
                result=None,
                created_at=now,
                expires_at=now + timedelta(hours=1),
            ),
            "action_receipts": dict(
                idempotency_key="ACT-LEGACY",
                action_kind="refund",
                result={"order_id": "ORD-LEGACY", "amount": 199, "refund_status": "requested"},
            ),
            "audit_events": dict(
                event_id="AUD-LEGACY",
                trace_id="TRC",
                actor_id="user",
                operation="refund.execute",
                resource_type="order",
                resource_id="ORD-LEGACY",
                outcome="success",
                details={},
                created_at=now,
            ),
        }
        with engine.begin() as connection:
            for name, row in rows.items():
                connection.execute(metadata.tables[name].insert().values(**row))
        # Nonempty legacy data has no provenance. A normal upgrade must not invent one.
        with pytest.raises(RuntimeError, match="legacy_tenant_mapping_required"):
            command.upgrade(cfg, "head")
        assert "tenant_id" not in {c["name"] for c in sa.inspect(engine).get_columns("orders")}
        cfg.cmd_opts = SimpleNamespace(x=["legacy_demo_backfill=true"])
        command.upgrade(cfg, "head")
        command.current(cfg, check_heads=True)
        command.check(cfg)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("select version_num from alembic_version")) == NEW_HEAD
            print("PostgreSQL:", connection.scalar(sa.text("select version()")))
            for name in TABLES:
                # Explicit compatibility assignment, not historically proven tenant identity.
                tenants = (
                    connection.execute(sa.text(f"select tenant_id from {name}")).scalars().all()
                )
                assert tenants == ["TENANT-DEMO"] * (2 if name == "audit_events" else 1)
        current = sa.MetaData()
        current.reflect(engine)
        for name, row in rows.items():
            column = next(
                c for c in sa.inspect(engine).get_columns(name) if c["name"] == "tenant_id"
            )
            assert column["nullable"] is False
            assert column["default"] is None
            invalid = dict(row)
            pk = next(iter(current.tables[name].primary_key.columns)).name
            invalid[pk] += "-NO-TENANT"
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(current.tables[name].insert().values(**invalid))
        command.downgrade(cfg, OLD_HEAD)
        for name in TABLES:
            assert "tenant_id" not in {c["name"] for c in sa.inspect(engine).get_columns(name)}
        command.upgrade(cfg, "head")
        command.current(cfg, check_heads=True)
        command.check(cfg)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("select version_num from alembic_version")) == NEW_HEAD
            for name in TABLES:
                assert connection.scalar(sa.text(f"select count(*) from {name}")) == (
                    3 if name == "audit_events" else 1
                )
                assert connection.scalar(sa.text(f"select tenant_id from {name}")) == "TENANT-DEMO"
        print("Upgrade/backfill/NOT NULL/current/drift/downgrade/re-upgrade: verified")
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
        root.dispose()


def test_empty_postgresql_database_upgrades_without_demo_opt_in(monkeypatch):
    url = make_url(URL)
    assert url.get_backend_name() == "postgresql" and url.database == "supportops_s2a"
    root = sa.create_engine(url)
    schema = "s2a_empty_" + uuid4().hex
    with root.begin() as connection:
        connection.execute(sa.schema.CreateSchema(schema))
    scoped = url.update_query_dict({"options": "-csearch_path=" + schema})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = sa.create_engine(scoped)
    cfg = Config("alembic.ini")
    cfg.cmd_opts = SimpleNamespace(x=[])
    try:
        command.upgrade(cfg, "head")
        command.check(cfg)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("select version_num from alembic_version")) == NEW_HEAD
            for name in TABLES:
                assert connection.scalar(sa.text(f"select count(*) from {name}")) == 0
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
        root.dispose()
