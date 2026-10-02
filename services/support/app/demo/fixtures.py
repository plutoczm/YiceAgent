"""Closed fixture contract and transactional import into an owned demo DB."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy import MetaData, Table, func, inspect, select, text
from sqlalchemy.engine import make_url

from app.store import Base, OrderRow

ROOT = Path(__file__).resolve().parents[4]
OWNER = "workflowops-demo"


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Customer(Closed):
    tenant_id: Literal["tenant-a", "tenant-b"]
    customer_id: str = Field(pattern=r"^customer-[ab][123]$")


class Order(Customer):
    order_id: str = Field(pattern=r"^ORD-[12][0-9]{3}$")
    status: Literal["delivered", "shipped"]
    amount: StrictInt = Field(ge=1, le=100000)
    refundable_amount: StrictInt = Field(ge=0, le=100000)
    refund_status: Literal["none"]
    return_status: Literal["none"]


class Scenarios(Closed):
    low_refund: str
    high_refund: str
    non_refundable: str
    other_customer: str
    other_tenant: str
    boundary_refund: str
    human_consultation: str = Field(min_length=1, max_length=1000)


class Fixture(Closed):
    fixture_version: Literal["v1"]
    generator_version: Literal["workflowops-fixture-1.0"]
    seed: StrictInt
    reference_time: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    currency: Literal["CNY"]
    synthetic: Literal[True]
    tenants: list[Literal["tenant-a", "tenant-b"]] = Field(min_length=2, max_length=2)
    customers: list[Customer] = Field(min_length=6, max_length=6)
    orders: list[Order] = Field(min_length=24, max_length=24)
    scenarios: Scenarios

    @model_validator(mode="after")
    def references(self):
        from datetime import datetime

        datetime.strptime(self.reference_time, "%Y-%m-%dT%H:%M:%SZ")
        expected = {(t, f"customer-{t[-1]}{c}") for t in self.tenants for c in range(1, 4)}
        actual = {(c.tenant_id, c.customer_id) for c in self.customers}
        if len(set(self.tenants)) != 2 or actual != expected:
            raise ValueError("invalid customer/tenant mapping")
        if len({o.order_id for o in self.orders}) != 24:
            raise ValueError("duplicate order")
        for o in self.orders:
            if (o.tenant_id, o.customer_id) not in actual:
                raise ValueError("invalid order reference")
            if o.refundable_amount > o.amount or (o.status == "shipped" and o.refundable_amount):
                raise ValueError("invalid refundable amount")
        for t, c in expected:
            if sum(o.tenant_id == t and o.customer_id == c for o in self.orders) != 4:
                raise ValueError("invalid order distribution")
        by_id = {o.order_id: o for o in self.orders}
        s = self.scenarios
        low, high, no, other, foreign, boundary = [
            by_id[k]
            for k in (
                s.low_refund,
                s.high_refund,
                s.non_refundable,
                s.other_customer,
                s.other_tenant,
                s.boundary_refund,
            )
        ]
        if not (
            low.tenant_id == high.tenant_id == no.tenant_id == boundary.tenant_id == "tenant-a"
            and low.customer_id == high.customer_id == no.customer_id == boundary.customer_id
            and low.refundable_amount == 199
            and high.refundable_amount == 899
            and no.refundable_amount == 0
            and boundary.refundable_amount == 500
            and other.tenant_id == low.tenant_id
            and other.customer_id != low.customer_id
            and foreign.tenant_id != low.tenant_id
        ):
            raise ValueError("invalid scenario semantics")
        return self


def load_fixture(directory):
    payload = (directory / "fixture.json").read_bytes()
    if len(payload) > 100000:
        raise ValueError("fixture too large")
    manifest = json.loads((directory / "fixture-manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(payload).hexdigest()
    schema = (ROOT / "contracts/demo_fixture/v1/schema.json").read_bytes()
    if (
        manifest.get("fixture_sha256") != digest
        or manifest.get("byte_count") != len(payload)
        or manifest.get("schema_sha256") != hashlib.sha256(schema).hexdigest()
        or manifest.get("order_count") != 24
        or manifest.get("fixture_version") != "v1"
        or manifest.get("generator_version") != "workflowops-fixture-1.0"
    ):
        raise ValueError("fixture manifest mismatch")
    if json.loads(schema) != Fixture.model_json_schema():
        raise ValueError("schema drift")
    return Fixture.model_validate(json.loads(payload)), digest


def validate_url(url):
    parsed = make_url(url)
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.host != "127.0.0.1"
        or parsed.port != 55432
        or parsed.database != "workflowops_demo"
        or parsed.username != "workflowops_demo"
        or parsed.query
    ):
        raise ValueError("not the WorkflowOps demo database")


def import_fixture(engine, fixture, digest, reset=False):
    fixture = Fixture.model_validate(fixture.model_dump())
    validate_url(engine.url)
    marker = Table("workflowops_demo_owner", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(7340202)"))
        allowed = set(Base.metadata.tables) | {"workflowops_demo_owner", "alembic_version"}
        if set(inspect(connection).get_table_names()) - allowed:
            raise ValueError("unexpected tables in demo database; refusing import/reset")
        owner = connection.execute(select(marker).with_for_update()).mappings().all()
        if len(owner) != 1 or owner[0]["owner"] != OWNER:
            raise ValueError("missing database ownership")
        occupied = any(
            connection.scalar(select(func.count()).select_from(table))
            for table in Base.metadata.sorted_tables
        )
        if not reset and owner[0]["fixture_hash"] == digest:
            return dict(
                status="no-op",
                fixture_hash=digest,
                order_count=connection.scalar(select(func.count()).select_from(OrderRow)),
            )
        if not reset and (occupied or owner[0]["fixture_hash"]):
            raise ValueError("nonempty demo database; explicit --reset-demo required")
        if reset:
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(table.delete())
        connection.execute(OrderRow.__table__.insert(), [o.model_dump() for o in fixture.orders])
        connection.execute(marker.update().values(fixture_hash=digest))
        return dict(status="imported", fixture_hash=digest, order_count=len(fixture.orders))
