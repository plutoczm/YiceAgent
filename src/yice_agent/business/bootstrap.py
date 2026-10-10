"""Explicit owned PostgreSQL business provisioning and resumable deterministic fixtures."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, insert, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from yice_agent.business.contracts import Confirmation, OrderAction, SimulationEvent
from yice_agent.business.models import Base, Order, Product, Version
from yice_agent.business.rules import PATHS, BusinessError, digest
from yice_agent.business.service import BusinessService
from yice_agent.config import OWNER
from yice_agent.identity import PERSONAS

SCHEMA_VERSION = 1
REFERENCE = datetime(2026, 9, 30, 12, tzinfo=UTC)


def validate_target(engine: Engine, schema: str) -> None:
    if (
        engine.dialect.name != "postgresql"
        or engine.url.host != "127.0.0.1"
        or engine.url.port != 55433
        or engine.url.database != "workflowops_v2"
    ):
        raise BusinessError("database_not_owned_v2")
    if schema != "public" and re.fullmatch(r"v2_test_[a-f0-9]{32}", schema) is None:
        raise BusinessError("business_schema_not_owned")
    with engine.connect() as connection:
        if connection.exec_driver_sql("SELECT owner FROM public.schema_owner").scalars().all() != [
            OWNER
        ]:
            raise BusinessError("database_owner_invalid")
        if connection.exec_driver_sql("SELECT current_schema()").scalar() != schema:
            raise BusinessError("business_search_path_mismatch")


def provision(engine: Engine, schema: str = "public") -> None:
    validate_target(engine, schema)
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {
                "key": int.from_bytes(
                    bytes.fromhex(digest(["business-schema", schema])[:16]), "big", signed=True
                )
            },
        )
        existing = set(inspect(connection).get_table_names(schema=schema))
        allowed = set(Base.metadata.tables) | ({"schema_owner"} if schema == "public" else set())
        if existing - allowed:
            raise BusinessError("unexpected_business_tables")
        if "business_version" in existing:
            marker = connection.execute(
                select(Version.owner, Version.version).where(Version.key == "business")
            ).one_or_none()
            if (
                marker is None
                or marker.owner != OWNER
                or marker.version != SCHEMA_VERSION
                or not set(Base.metadata.tables) <= existing
            ):
                raise BusinessError("business_schema_version_invalid")
            return
        if existing - ({"schema_owner"} if schema == "public" else set()):
            raise BusinessError("unmarked_business_data")
        Base.metadata.create_all(connection)
        connection.execute(
            insert(Version).values(
                key="business",
                owner=OWNER,
                version=SCHEMA_VERSION,
                seed_hash=None,
                seed_complete=False,
            )
        )


def ready(engine: Engine) -> bool:
    with Session(engine) as session:
        marker = session.get(Version, "business")
        return bool(
            marker
            and marker.owner == OWNER
            and marker.version == SCHEMA_VERSION
            and marker.seed_complete
        )


def fixture(seed: int = 42) -> dict[str, Any]:
    if seed != 42:
        raise BusinessError("unsupported_demo_seed", 422)
    products = []
    orders = []
    progression = {}
    catalog = {
        "ecommerce": [
            ("CABLE", "充电线", "19.90"),
            ("KETTLE", "电热水壶", "199.00"),
            ("PHONE", "演示手机", "899.00"),
            ("BASE", "标准商品", "100.00"),
        ],
        "food": [
            ("TEA", "奶茶", "19.90"),
            ("MEAL", "套餐", "39.90"),
            ("FAMILY", "家庭套餐", "129.00"),
            ("BASE", "标准餐品", "100.00"),
        ],
    }
    for tenant in ("a", "b"):
        for kind, goods in catalog.items():
            prefix = "ECO" if kind == "ecommerce" else "FOOD"
            for sku, name, price in goods:
                products.append(
                    {
                        "id": f"{prefix}-{tenant.upper()}-{sku}",
                        "tenant": "tenant-" + tenant,
                        "merchant": f"{prefix}-{tenant.upper()}-SHOP",
                        "order_type": kind,
                        "name": name,
                        "price": price,
                    }
                )
            targets = list(PATHS[kind])
            if kind == "ecommerce":
                targets.append("paid")
            for index, target in enumerate(targets, 1):
                selected = goods[(index - 1) % 3]
                order_id = f"{prefix}-{tenant.upper()}-{index:02d}"
                orders.append(
                    {
                        "id": order_id,
                        "tenant": "tenant-" + tenant,
                        "customer": f"customer-{tenant}{1 if index % 2 else 2}",
                        "merchant": f"{prefix}-{tenant.upper()}-SHOP",
                        "order_type": kind,
                        "product_id": f"{prefix}-{tenant.upper()}-{selected[0]}",
                        "name": selected[1],
                        "price": selected[2],
                    }
                )
                progression[order_id] = target
    return {
        "seed": seed,
        "version": "orders-demo/v2.1",
        "products": products,
        "orders": orders,
        "progression": progression,
    }


def request_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, "workflowops-v2-seed:" + value))


def seed(engine: Engine, schema: str = "public") -> dict[str, Any]:
    validate_target(engine, schema)
    data = fixture()
    fingerprint = digest(data)
    with Session(engine) as session, session.begin():
        marker = session.scalar(select(Version).where(Version.key == "business").with_for_update())
        if marker is None:
            raise BusinessError("business_schema_not_initialized")
        if marker.seed_hash:
            if marker.seed_hash != fingerprint:
                raise BusinessError("demo_fixture_conflict")
            if marker.seed_complete:
                return {"status": "no_op", "fixture_hash": fingerprint, "orders": 24}
        else:
            if session.scalar(select(func.count()).select_from(Product)) or session.scalar(
                select(func.count()).select_from(Order)
            ):
                raise BusinessError("seed_target_nonempty")
            for product in data["products"]:
                session.add(
                    Product(
                        **{**product, "price": Decimal(product["price"])}, version=1, active=True
                    )
                )
            for row in data["orders"]:
                session.add(
                    Order(
                        id=row["id"],
                        tenant=row["tenant"],
                        customer=row["customer"],
                        merchant=row["merchant"],
                        order_type=row["order_type"],
                        status="awaiting_payment",
                        payment_status="unpaid",
                        refund_status="none",
                        total=Decimal(row["price"]),
                        paid=Decimal("0.00"),
                        refund_requested=Decimal("0.00"),
                        refunded=Decimal("0.00"),
                        items=[
                            {
                                "product_id": row["product_id"],
                                "name": row["name"],
                                "quantity": 1,
                                "unit_price": row["price"],
                                "product_version": 1,
                            }
                        ],
                        note="固定 seed 模拟订单",
                        version=1,
                        created_at=REFERENCE,
                        updated_at=REFERENCE,
                    )
                )
            marker.seed_hash = fingerprint
    service = BusinessService(engine, clock=lambda: REFERENCE)
    for row in data["orders"]:
        target = data["progression"][row["id"]]
        if target == "awaiting_payment":
            continue
        actor = PERSONAS[row["customer"]]
        operator = PERSONAS["operator-" + row["tenant"][-1]]
        draft = service.propose(
            actor,
            OrderAction(
                kind="pay", order_id=row["id"], client_request_id=request_id(row["id"] + ":pay")
            ),
        )
        service.confirm(
            actor,
            draft["action_id"],
            Confirmation(
                confirm=True,
                expected_version=1,
                client_request_id=request_id(row["id"] + ":confirm-pay"),
            ),
        )
        service.simulate(
            operator,
            SimulationEvent(
                order_id=row["id"],
                event="payment_succeeded",
                expected_version=2,
                client_request_id=request_id(row["id"] + ":paid"),
            ),
        )
        for step in range(PATHS[row["order_type"]].index(target) - 1):
            service.simulate(
                operator,
                SimulationEvent(
                    order_id=row["id"],
                    event="advance",
                    expected_version=3 + step,
                    client_request_id=request_id(row["id"] + ":advance:" + str(step)),
                ),
            )
    with Session(engine) as session, session.begin():
        marker = session.scalar(select(Version).where(Version.key == "business").with_for_update())
        assert marker is not None
        marker.seed_complete = True
    return {
        "status": "seeded",
        "fixture_hash": fingerprint,
        "orders": 24,
        "source": "synthetic_fixture_and_real_domain_simulator",
    }


def prepare(engine: Engine, schema: str = "public") -> dict[str, Any]:
    provision(engine, schema)
    return seed(engine, schema)
