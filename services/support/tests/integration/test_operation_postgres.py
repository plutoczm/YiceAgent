"""PostgreSQL is the acceptance backend for operation locking and atomicity."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from app.auth import Principal
from app.config import Settings
from app.container import build_container
from app.domain import OperationStatus, PendingActionKind
from app.operations import CustomerContext, OperationConflict
from app.store import (
    ActionReceiptRow,
    ApprovalDecisionRow,
    AuditEventRow,
    OrderRow,
    PendingActionRow,
)

URL = os.getenv("S2B_POSTGRES_TEST_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="requires explicitly isolated PostgreSQL operation DB"),
]
TENANT = "TENANT-DEMO"


@pytest.fixture
def database(monkeypatch):
    url = make_url(URL)
    assert url.get_backend_name() == "postgresql"
    assert url.database in {"supportops_s2a", "supportops_s2b"}
    root = sa.create_engine(url)
    schema = "s2b_" + uuid4().hex
    with root.begin() as connection:
        connection.execute(sa.schema.CreateSchema(schema))
    scoped = url.update_query_dict({"options": "-csearch_path=" + schema})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = sa.create_engine(scoped)
    config = Config("alembic.ini")
    config.cmd_opts = SimpleNamespace(x=[])
    try:
        yield scoped, engine, config
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
        root.dispose()


@pytest.fixture
def services(database):
    url, _, config = database
    command.upgrade(config, "head")
    c = build_container(
        Settings(
            database_url=url.render_as_string(hide_password=False),
            database_auto_create_schema=False,
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
    )
    yield c
    c.observability.shutdown()
    c.store.engine.dispose()


def ctx(high=False, tenant=TENANT):
    return CustomerContext("CUST-002" if high else "CUST-001", "customer", "TRC-PG", tenant)


def admin(subject="admin", tenant=TENANT):
    return Principal(
        subject=subject,
        tenant_id=tenant,
        customer_id=None,
        roles=frozenset({"support_admin"}),
        authentication_method="jwt",
    )


def prepare(c, high=False, kind=PendingActionKind.REFUND):
    return c.operations.prepare(
        kind=kind,
        order_id="ORD-2001" if high else "ORD-1001",
        conversation_id="CONV-PG",
        context=ctx(high),
    ).operation


def counts(c, operation_id):
    with c.store._session() as session:
        receipt = session.scalars(
            sa.select(ActionReceiptRow).where(ActionReceiptRow.operation_id == operation_id)
        ).all()
        audits = session.scalars(
            sa.select(AuditEventRow).where(
                AuditEventRow.operation_id == operation_id,
                AuditEventRow.operation == "operation.submitted",
            )
        ).all()
        return len(receipt), len(audits)


@pytest.mark.parametrize(
    "kind,high",
    [
        (PendingActionKind.REFUND, False),
        (PendingActionKind.REFUND, True),
        (PendingActionKind.RETURN_REQUEST, False),
    ],
)
def test_concurrent_preparation_has_one_business_operation(services, kind, high):
    barrier = Barrier(4)

    def run():
        barrier.wait(timeout=10)
        return prepare(services, high, kind)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result(timeout=20) for f in [pool.submit(run) for _ in range(4)]]
    assert len({r.operation_id for r in results}) == 1
    with services.store._session() as session:
        assert len(session.scalars(sa.select(PendingActionRow)).all()) == 1
        from app.store import TicketRow

        assert len(session.scalars(sa.select(TicketRow)).all()) == int(high)


def test_concurrent_confirmation_and_terminal_replay_submit_once(services):
    operation = prepare(services)
    barrier = Barrier(4)

    def run():
        barrier.wait(timeout=10)
        return services.operations.resolve(operation.operation_id, confirm=True, context=ctx())

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result(timeout=20) for f in [pool.submit(run) for _ in range(4)]]
    assert all(r.status is OperationStatus.SUBMITTED for r in results)
    assert all(r.result == results[0].result and r.version == results[0].version for r in results)
    assert counts(services, operation.operation_id) == (1, 1)


def test_confirmation_cancellation_race_has_one_terminal_winner(services):
    operation = prepare(services)
    barrier = Barrier(2)

    def run(confirm):
        barrier.wait(timeout=10)
        return services.operations.resolve(operation.operation_id, confirm=confirm, context=ctx())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, flag) for flag in [True, False]]
        results = [f.result(timeout=20) for f in futures]
    assert results[0].status == results[1].status
    assert results[0].result == results[1].result
    assert results[0].status in {OperationStatus.SUBMITTED, OperationStatus.CANCELLED}
    n = int(results[0].status is OperationStatus.SUBMITTED)
    assert counts(services, operation.operation_id) == (n, n)


@pytest.mark.parametrize("decisions", [("approve", "approve"), ("approve", "reject")])
def test_concurrent_human_decisions_have_one_durable_winner(services, decisions):
    operation = prepare(services, True)
    confirmed = services.operations.resolve(operation.operation_id, confirm=True, context=ctx(True))
    barrier = Barrier(2)

    def run(index):
        barrier.wait(timeout=10)
        try:
            return services.operations.decide(
                operation.operation_id,
                decision=decisions[index],
                expected_version=confirmed.version,
                principal=admin(f"admin-{index}"),
                trace_id="TRC-PG",
            )
        except OperationConflict as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=20) for f in [pool.submit(run, i) for i in range(2)]]
    successful = [r for r in results if not isinstance(r, str)]
    if decisions[0] == decisions[1]:
        assert len(successful) == 2 and successful[0].result == successful[1].result
    else:
        assert len(successful) == 1
        assert "approval_decision_conflict" in results
    with services.store._session() as session:
        approvals = session.scalars(sa.select(ApprovalDecisionRow)).all()
        assert len(approvals) == 1 and approvals[0].actor_id in {"admin-0", "admin-1"}
        status = session.get(PendingActionRow, operation.operation_id).status
        assert status == ("submitted" if approvals[0].decision == "approve" else "rejected")
        assert counts(services, operation.operation_id) == (
            (1, 1) if status == "submitted" else (0, 0)
        )


def limited_service(c):
    url = make_url(c.settings.database_url)
    options = url.query["options"] + " -clock_timeout=200ms"
    return build_container(
        Settings(
            database_url=url.update_query_dict({"options": options}).render_as_string(
                hide_password=False
            ),
            database_auto_create_schema=False,
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
        )
    )


@pytest.mark.parametrize(
    "decision,mutate", [("confirm", True), ("approve", True), ("confirm", False)]
)
def test_order_lock_conflict_rolls_back_then_retry_reads_authoritative_state(
    services, decision, mutate
):
    high = decision == "approve"
    operation = prepare(services, high)
    if high:
        operation = services.operations.resolve(
            operation.operation_id, confirm=True, context=ctx(True)
        )
    c = limited_service(services)
    entered = Event()

    def invoke():
        entered.set()
        if decision == "approve":
            return c.operations.decide(
                operation.operation_id,
                decision="approve",
                expected_version=operation.version,
                principal=admin(),
                trace_id="TRC-RETRY",
            )
        return c.operations.resolve(operation.operation_id, confirm=True, context=ctx())

    try:
        with services.store.engine.begin() as owner:
            owner.execute(
                sa.select(OrderRow).where(OrderRow.order_id == operation.order_id).with_for_update()
            )
            if mutate:
                owner.execute(
                    sa.update(OrderRow)
                    .where(OrderRow.order_id == operation.order_id)
                    .values(refundable_amount=1999)
                )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(invoke)
                assert entered.wait(5)
                with pytest.raises(OperationalError) as error:
                    future.result(timeout=10)
                assert error.value.orig.sqlstate == "55P03"
            assert counts(services, operation.operation_id) == (0, 0)
        retried = invoke()
        assert retried.status is (
            OperationStatus.INVALIDATED if mutate else OperationStatus.SUBMITTED
        )
        assert counts(services, operation.operation_id) == ((0, 0) if mutate else (1, 1))
        if high:
            with services.store._session() as session:
                assert session.get(ApprovalDecisionRow, operation.operation_id) is None
    finally:
        c.observability.shutdown()
        c.store.engine.dispose()


def test_confirmation_holds_order_lock_through_local_commit(services, monkeypatch):
    operation = prepare(services)
    locked, release = Event(), Event()

    def checkpoint(stage):
        if stage == "before_order_update":
            locked.set()
            assert release.wait(10)

    monkeypatch.setattr(services.operations, "_checkpoint", checkpoint)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            services.operations.resolve, operation.operation_id, confirm=True, context=ctx()
        )
        try:
            assert locked.wait(5)
            with pytest.raises(OperationalError) as error:
                with services.store.engine.begin() as other:
                    other.execute(sa.text("SET LOCAL lock_timeout='200ms'"))
                    other.execute(
                        sa.update(OrderRow)
                        .where(OrderRow.order_id == "ORD-1001")
                        .values(refundable_amount=899)
                    )
            assert error.value.orig.sqlstate == "55P03"
        finally:
            release.set()
        assert future.result(timeout=10).result["amount"] == 199
    assert counts(services, operation.operation_id) == (1, 1)


def test_partial_unique_index_rejects_concurrent_uncoordinated_inserts(services):
    original = prepare(services)
    services.operations.resolve(original.operation_id, confirm=False, context=ctx())
    with services.store._session() as session:
        row = session.get(PendingActionRow, original.operation_id)
        values = {
            column.name: getattr(row, column.name) for column in PendingActionRow.__table__.columns
        }
    values.update(status="pending_confirmation", version=1, result=None, terminal_at=None)
    barrier = Barrier(2)

    def insert():
        barrier.wait(timeout=10)
        try:
            with services.store._session.begin() as session:
                session.add(PendingActionRow(**{**values, "action_id": "ACT-" + uuid4().hex[:12]}))
            return "inserted"
        except IntegrityError as exc:
            assert exc.orig.sqlstate == "23505"
            assert exc.orig.diag.constraint_name == "uq_operation_business_intent"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=20) for f in [pool.submit(insert) for _ in range(2)]]
    assert sorted(results) == ["conflict", "inserted"]


@pytest.mark.parametrize(
    "stage",
    [
        "before_order_update",
        "after_order_update",
        "after_receipt",
        "after_audit",
        "after_terminal_update",
    ],
)
def test_real_postgresql_transaction_failure_has_no_partial_commit(services, stage, monkeypatch):
    operation = prepare(services)

    def checkpoint(current):
        if current == stage:
            raise RuntimeError("crash_probe")

    monkeypatch.setattr(services.operations, "_checkpoint", checkpoint)
    with pytest.raises(RuntimeError, match="crash_probe"):
        services.operations.resolve(operation.operation_id, confirm=True, context=ctx())
    assert counts(services, operation.operation_id) == (0, 0)
    with services.store._session() as session:
        assert session.get(OrderRow, "ORD-1001").refund_status == "none"
        row = session.get(PendingActionRow, operation.operation_id)
        assert (
            row.status == "pending_confirmation" and row.confirmed_at is None and row.version == 1
        )
    monkeypatch.setattr(services.operations, "_checkpoint", lambda _: None)
    assert (
        services.operations.resolve(operation.operation_id, confirm=True, context=ctx()).status
        is OperationStatus.SUBMITTED
    )
    assert counts(services, operation.operation_id) == (1, 1)


def test_database_constraints_and_cross_tenant_relationships(services):
    operation = prepare(services)
    with services.store._session() as session:
        row = session.get(PendingActionRow, operation.operation_id)
        values = {
            column.name: getattr(row, column.name) for column in PendingActionRow.__table__.columns
        }
    for changes, expected in zip(
        [
            {"action_id": "ACT-BAD-STATUS", "status": "executing"},
            {"action_id": "ACT-BAD-SUBMITTED", "status": "submitted", "result": {}},
            {"action_id": "ACT-BAD-QUOTE", "quoted_amount": None},
            {"action_id": "ACT-BAD-LEGACY", "legacy": True},
            {
                "action_id": "ACT-BAD-CONFIRM",
                "confirmed_at": datetime.now(UTC),
                "confirmed_by": "user",
            },
        ],
        [
            "ck_operation_status",
            "ck_operation_submission",
            "ck_operation_quote",
            "ck_operation_legacy_terminal",
            "ck_operation_confirmation",
        ],
        strict=True,
    ):
        with pytest.raises(IntegrityError) as error, services.store._session.begin() as session:
            session.add(PendingActionRow(**{**values, **changes}))
        assert error.value.orig.diag.constraint_name == expected
    for tenant, kind in [("TENANT-OTHER", "refund"), (TENANT, "return_request")]:
        with pytest.raises(IntegrityError), services.store._session.begin() as session:
            session.add(
                ActionReceiptRow(
                    tenant_id=tenant,
                    operation_id=operation.operation_id,
                    idempotency_key=operation.operation_id,
                    legacy=False,
                    action_kind=kind,
                    result={"order_id": "ORD-1001"},
                )
            )
    with pytest.raises(IntegrityError), services.store._session.begin() as session:
        session.add(
            ApprovalDecisionRow(
                operation_id=operation.operation_id,
                tenant_id="TENANT-OTHER",
                decision="approve",
                actor_id="admin",
                decided_at=datetime.now(UTC),
                operation_version=1,
                snapshot_version=1,
                snapshot_hash=values["snapshot_hash"],
                snapshot=values["snapshot"],
            )
        )


def test_legacy_migration_preserves_evidence_without_inventing_authority(database):
    _, engine, config = database
    command.upgrade(config, "20260915_0004")
    metadata = sa.MetaData()
    metadata.reflect(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            metadata.tables["orders"]
            .insert()
            .values(
                tenant_id=TENANT,
                order_id="ORD-LEGACY",
                customer_id="CUST-001",
                status="delivered",
                amount=199,
                refundable_amount=199,
                refund_status="requested",
                return_status="none",
            )
        )
        for name, status in [
            ("PENDING", "pending"),
            ("SUBMITTED", "executed"),
            ("BAD", "executed"),
        ]:
            result = {"order_id": "ORD-LEGACY", "refund_status": "requested", "amount": 199}
            connection.execute(
                metadata.tables["pending_actions"]
                .insert()
                .values(
                    tenant_id=TENANT,
                    action_id="ACT-" + name,
                    conversation_id="LEGACY",
                    customer_id="CUST-001",
                    kind="refund",
                    payload={"order_id": "ORD-LEGACY", "amount": 199},
                    status=status,
                    result=result if status == "executed" else None,
                    created_at=now,
                    expires_at=now + timedelta(hours=1),
                )
            )
            connection.execute(
                metadata.tables["action_receipts"]
                .insert()
                .values(
                    tenant_id=TENANT,
                    idempotency_key="ACT-" + name,
                    action_kind="refund",
                    result={**result, "amount": 899} if name == "BAD" else result,
                )
            )
        connection.execute(
            metadata.tables["action_receipts"]
            .insert()
            .values(
                tenant_id=TENANT,
                idempotency_key="ACT-ORPHAN",
                action_kind="refund",
                result={"legacy": "unknown"},
            )
        )
    command.upgrade(config, "head")
    command.current(config, check_heads=True)
    command.check(config)
    with engine.connect() as connection:
        rows = {
            row.action_id: row for row in connection.execute(sa.select(PendingActionRow.__table__))
        }
        assert rows["ACT-PENDING"].status == "invalidated"
        assert rows["ACT-BAD"].status == "invalidated"
        assert rows["ACT-SUBMITTED"].status == "submitted"
        assert all(
            row.legacy and row.confirmed_at is None and row.confirmed_by is None
            for row in rows.values()
        )
        assert all(
            row.snapshot_version == 0 and row.snapshot["approval_requirement"] == "unknown"
            for row in rows.values()
        )
        assert connection.scalar(sa.select(sa.func.count()).select_from(ApprovalDecisionRow)) == 0
        receipts = {
            row.idempotency_key: row
            for row in connection.execute(sa.select(ActionReceiptRow.__table__))
        }
        assert (
            receipts["ACT-ORPHAN"].operation_id is None and receipts["ACT-BAD"].operation_id is None
        )
        assert receipts["ACT-SUBMITTED"].operation_id == "ACT-SUBMITTED"
        assert len(receipts) == 4
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.operation == "operation.legacy_mapped")
            )
            == 3
        )
    command.downgrade(config, "20260915_0004")
    command.upgrade(config, "head")
    command.current(config, check_heads=True)
    command.check(config)
    with engine.connect() as connection:
        assert (
            connection.scalar(sa.text("select version_num from alembic_version")) == "20260917_0005"
        )
        assert connection.scalar(sa.select(sa.func.count()).select_from(PendingActionRow)) == 3
        assert connection.scalar(sa.select(sa.func.count()).select_from(ActionReceiptRow)) == 4
        assert connection.scalar(sa.select(sa.func.count()).select_from(ApprovalDecisionRow)) == 0
        print("PostgreSQL:", connection.scalar(sa.text("select version()")))


def test_downgrade_refuses_to_destroy_new_operation_evidence(services, database):
    _, engine, config = database
    operation = prepare(services)
    with pytest.raises(RuntimeError, match="operation_downgrade_requires_no_new_operations"):
        command.downgrade(config, "20260915_0004")
    with engine.connect() as connection:
        assert (
            connection.scalar(sa.text("select version_num from alembic_version")) == "20260917_0005"
        )
    assert (
        services.operations.get(operation.operation_id, principal=admin()).status
        is OperationStatus.PENDING_CONFIRMATION
    )


def test_approval_submission_failure_rolls_back_decision_but_keeps_prior_customer_consent(
    services, monkeypatch
):
    operation = prepare(services, True)
    confirmed = services.operations.resolve(operation.operation_id, confirm=True, context=ctx(True))

    def fail(stage):
        if stage == "after_receipt":
            raise RuntimeError("approval_commit_failure")

    monkeypatch.setattr(services.operations, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="approval_commit_failure"):
        services.operations.decide(
            operation.operation_id,
            decision="approve",
            expected_version=confirmed.version,
            principal=admin(),
            trace_id="TRC-FAIL",
        )
    with services.store._session() as session:
        row = session.get(PendingActionRow, operation.operation_id)
        assert row.status == "pending_approval" and row.confirmed_at is not None
        assert row.version == confirmed.version
        assert session.get(ApprovalDecisionRow, operation.operation_id) is None
        assert session.get(OrderRow, "ORD-2001").refund_status == "none"
    assert counts(services, operation.operation_id) == (0, 0)
    monkeypatch.setattr(services.operations, "_checkpoint", lambda _: None)
    result = services.operations.decide(
        operation.operation_id,
        decision="approve",
        expected_version=confirmed.version,
        principal=admin(),
        trace_id="TRC-RETRY",
    )
    assert result.status is OperationStatus.SUBMITTED
    assert counts(services, operation.operation_id) == (1, 1)
