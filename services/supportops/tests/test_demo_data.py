import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text

from app.demo.fixtures import ROOT, Fixture, import_fixture, load_fixture, validate_url
from app.store import Base, OrderRow, SupportStore


def bound_store(database):
    from sqlalchemy.orm import sessionmaker

    store = SupportStore(str(database.url.render_as_string(hide_password=False)))
    store.engine.dispose()
    store.engine = database
    store._session = sessionmaker(bind=database, expire_on_commit=False)
    return store


def sample():
    return json.loads((ROOT / "demo/expected/fixture-v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "tenant",
        "customer",
        "amount",
        "float",
        "extra",
        "scenario",
        "date",
        "refundable",
        "version",
    ],
)
def test_invalid_fixture(change):
    value = sample()
    if change == "duplicate":
        value["orders"][1]["order_id"] = value["orders"][0]["order_id"]
    elif change == "tenant":
        value["orders"][0]["tenant_id"] = "tenant-b"
    elif change == "customer":
        value["customers"][0]["customer_id"] = "customer-b1"
    elif change == "amount":
        value["orders"][0]["amount"] = -1
    elif change == "float":
        value["orders"][0]["amount"] = 199.0
    elif change == "extra":
        value["orders"][0]["approved"] = True
    elif change == "scenario":
        value["scenarios"]["low_refund"] = "ORD-1002"
    elif change == "date":
        value["reference_time"] = "2026-99-01T00:00:00Z"
    elif change == "refundable":
        value["orders"][0]["refundable_amount"] = 200
    else:
        value["fixture_version"] = "v2"
    with pytest.raises((ValueError, KeyError)):
        Fixture.model_validate(value)


def test_hash_manifest_and_schema(tmp_path):
    payload = (ROOT / "demo/expected/fixture-v1.json").read_bytes()
    manifest = dict(
        fixture_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        schema_sha256=hashlib.sha256(
            (ROOT / "contracts/demo_fixture/v1/schema.json").read_bytes()
        ).hexdigest(),
        order_count=24,
        fixture_version="v1",
        generator_version="workflowops-fixture-1.0",
    )
    (tmp_path / "fixture.json").write_bytes(payload)
    (tmp_path / "fixture-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert len(load_fixture(tmp_path)[0].orders) == 24
    (tmp_path / "fixture.json").write_bytes(payload + b" ")
    with pytest.raises(ValueError, match="manifest"):
        load_fixture(tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///supportops.db",
        "postgresql+psycopg://workflowops_demo@127.0.0.1:5432/workflowops_demo",
        "postgresql+psycopg://workflowops_demo@remote:55432/workflowops_demo",
        "postgresql+psycopg://workflowops_demo@127.0.0.1:55432/original",
        "postgresql+psycopg://workflowops_demo@127.0.0.1:55432/workflowops_demo?options=unsafe",
    ],
)
def test_foreign_database_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.fixture
def database():
    config = json.loads((ROOT / ".runtime/demo-db.json").read_text(encoding="utf-8"))
    validate_url(config["database_url"])
    root = create_engine(config["database_url"])
    schema = "p2_test_" + uuid4().hex
    with root.begin() as connection:
        assert connection.execute(text("SELECT owner FROM workflowops_demo_owner")).scalar() == (
            "workflowops-demo"
        )
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        config["database_url"], connect_args={"options": f"-csearch_path={schema}"}
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE workflowops_demo_owner(owner TEXT PRIMARY KEY, fixture_hash TEXT)")
        )
        connection.execute(
            text("INSERT INTO workflowops_demo_owner VALUES ('workflowops-demo',NULL)")
        )
    try:
        yield engine
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose()


def test_real_postgres_import_noop_reset_and_isolation(database):
    fixture = Fixture.model_validate(sample())
    assert import_fixture(database, fixture, "a" * 64)["status"] == "imported"
    assert import_fixture(database, fixture, "a" * 64)["status"] == "no-op"
    with pytest.raises(ValueError, match="nonempty"):
        import_fixture(database, fixture, "b" * 64)
    with database.connect() as c:
        assert c.scalar(select(func.count()).select_from(OrderRow)) == 24
    store = bound_store(database)
    assert store.get_order("ORD-1001", "customer-a1", tenant_id="tenant-a").amount == 199
    assert store.get_order("ORD-1001", "customer-a2", tenant_id="tenant-a") is None
    assert store.get_order("ORD-1001", "customer-a1", tenant_id="tenant-b") is None
    assert import_fixture(database, fixture, "b" * 64, reset=True)["status"] == "imported"
    with database.connect() as c:
        assert c.scalar(select(func.count()).select_from(OrderRow)) == 24
        for table in Base.metadata.sorted_tables:
            if table.name != "orders":
                assert c.scalar(select(func.count()).select_from(table)) == 0


def test_reset_ownership_and_transaction_rollback(database):
    fixture = Fixture.model_validate(sample())
    import_fixture(database, fixture, "a" * 64)
    from sqlalchemy import event

    def fail_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO orders"):
            raise RuntimeError("injected insert failure after reset deletes")

    event.listen(database, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            import_fixture(database, fixture, "b" * 64, reset=True)
    finally:
        event.remove(database, "before_cursor_execute", fail_insert)
    with database.begin() as c:
        assert c.scalar(select(func.count()).select_from(OrderRow)) == 24
        assert (
            c.execute(text("SELECT fixture_hash FROM workflowops_demo_owner")).scalar() == "a" * 64
        )
        c.execute(text("UPDATE workflowops_demo_owner SET owner='foreign'"))
    with pytest.raises(ValueError, match="ownership"):
        import_fixture(database, fixture, "b" * 64, reset=True)
    with database.connect() as c:
        assert c.scalar(select(func.count()).select_from(OrderRow)) == 24


def test_fixture_scenarios_use_real_workflows_and_runtime_clock(database):
    from datetime import UTC, datetime, timedelta

    from app.domain import PolicyAction
    from app.operations import CustomerContext
    from app.policy import ActionPolicy
    from app.reliability import LocalReliabilityCoordinator
    from app.tickets import TicketWorkflow
    from app.workflows import OrderLookupRequest, RefundPreparationRequest, SupportWorkflows

    fixture = Fixture.model_validate(sample())
    import_fixture(database, fixture, "a" * 64)
    store = bound_store(database)
    workflows = SupportWorkflows(
        store=store,
        tickets=TicketWorkflow(store),
        policy=ActionPolicy(500),
        reliability=LocalReliabilityCoordinator(),
        action_confirmation_ttl_seconds=300,
    )
    context = CustomerContext("customer-a1", "customer-a1", "p2-test", "tenant-a")
    before = datetime.now(UTC)
    for order, expected in (
        ("ORD-1001", PolicyAction.REQUIRE_CONFIRMATION),
        ("ORD-1002", PolicyAction.REQUIRE_HUMAN),
        ("ORD-1004", PolicyAction.REQUIRE_CONFIRMATION),
    ):
        eligibility = workflows.refund_eligibility(OrderLookupRequest(order), context=context)
        assert eligibility.eligible and eligibility.decision.action == expected
    assert not workflows.refund_eligibility(
        OrderLookupRequest("ORD-1003"), context=context
    ).eligible
    low = workflows.prepare_refund(RefundPreparationRequest("ORD-1001", "low"), context=context)
    high = workflows.prepare_refund(RefundPreparationRequest("ORD-1002", "high"), context=context)
    assert low.operation.status.value == "pending_confirmation"
    assert high.operation.status.value == "pending_approval"
    assert low.operation.quoted_amount == 199 and high.operation.quoted_amount == 899
    assert before < low.operation.expires_at <= datetime.now(UTC) + timedelta(seconds=301)
    ticket = workflows.create_ticket(
        context=context, conversation_id="human", reason=fixture.scenarios.human_consultation
    )
    assert ticket.status.value == "open" and before <= ticket.created_at <= datetime.now(UTC)
    assert ticket.ticket_id != high.ticket.ticket_id
    assert import_fixture(database, fixture, "a" * 64)["status"] == "no-op"
    assert store.get_order("ORD-1001", "customer-a1", tenant_id="tenant-a").refund_status == "none"


def test_nonempty_without_hash_and_unexpected_tables_refused(database):
    fixture = Fixture.model_validate(sample())
    with database.begin() as c:
        c.execute(OrderRow.__table__.insert(), fixture.orders[0].model_dump())
    with pytest.raises(ValueError, match="nonempty"):
        import_fixture(database, fixture, "a" * 64)
    with database.begin() as c:
        assert c.scalar(select(func.count()).select_from(OrderRow)) == 1
        c.execute(text("CREATE TABLE unknown_user_data(id INTEGER)"))
    with pytest.raises(ValueError, match="unexpected tables"):
        import_fixture(database, fixture, "a" * 64, reset=True)
    with database.connect() as c:
        assert c.scalar(select(func.count()).select_from(OrderRow)) == 1


def test_concurrent_import_has_one_writer(database):
    from concurrent.futures import ThreadPoolExecutor

    fixture = Fixture.model_validate(sample())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_fixture(database, fixture, "a" * 64), range(2)))
    assert sorted(r["status"] for r in results) == ["imported", "no-op"]
    with database.connect() as c:
        assert c.scalar(select(func.count()).select_from(OrderRow)) == 24
