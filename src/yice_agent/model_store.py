"""Owned PostgreSQL call metadata and conservative, persistent CNY reservations."""

import hashlib
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, MetaData, Numeric, String, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from yice_agent.config import OWNER, Settings
from yice_agent.contracts import GenerationRequest, Usage

SCHEMA = "workflowops_models"
CAP = Decimal("15.00")
QUANTUM = Decimal("0.000001")


class ModelError(RuntimeError):
    """Fixed category only: provider bodies and secrets are never error messages."""


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class Version(Base):
    __tablename__ = "schema_version"
    owner: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(16))


class Call(Base):
    __tablename__ = "model_calls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    model_id: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    reserved: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    charged: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    usage: Mapped[dict[str, Any]] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def cost(input_tokens: int, output_tokens: int, settings: Settings) -> Decimal:
    if not settings.input_price_cny_per_million or not settings.output_price_cny_per_million:
        raise ModelError("reliable_rates_required")
    rates = [
        Decimal(settings.input_price_cny_per_million),
        Decimal(settings.output_price_cny_per_million),
    ]
    if any(not x.is_finite() or x <= 0 for x in rates):
        raise ModelError("reliable_rates_required")
    return ((input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000).quantize(
        QUANTUM, rounding=ROUND_CEILING
    )


def validate_owned(engine: Engine) -> None:
    if (
        engine.url.host != "127.0.0.1"
        or engine.url.port != 55433
        or engine.url.database != "workflowops_v2"
        or engine.dialect.name != "postgresql"
    ):
        raise ModelError("database_not_owned")
    with engine.connect() as c:
        if c.exec_driver_sql("SELECT owner FROM public.schema_owner").scalars().all() != [OWNER]:
            raise ModelError("database_owner_invalid")


def prepare(engine: Engine) -> None:
    validate_owned(engine)
    with engine.begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(2026100403)"))
        was_present = "workflowops_models" in inspect(c).get_schema_names()
        c.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS workflowops_models")
        existing = set(inspect(c).get_table_names(schema=SCHEMA))
        if was_present and not existing:
            raise ModelError("unmarked_schema_unowned")
        if existing:
            allowed = {t.name for t in Base.metadata.tables.values()}
            if existing != allowed:
                raise ModelError("model_schema_unowned")
            marker = c.execute(select(Version)).mappings().one_or_none()
            if marker is None or marker["owner"] != OWNER or marker["version"] != "1":
                raise ModelError("model_schema_unowned")
            return
        Base.metadata.create_all(c)
        c.execute(Version.__table__.insert().values(owner=OWNER, version="1"))


class ModelStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    def begin(
        self,
        scope: str,
        channel: str,
        model_id: str,
        request: GenerationRequest,
        settings: Settings,
    ) -> str:
        if channel not in {"qwen", "chatgpt", "deepseek"}:
            raise ModelError("unsupported_billable_channel")
        if channel == "deepseek":
            from datetime import date
            from zoneinfo import ZoneInfo

            if not settings.pricing_checked_on or not settings.pricing_source:
                raise ModelError("reliable_rates_required")
            try:
                age = (
                    datetime.now(ZoneInfo("Asia/Shanghai")).date()
                    - date.fromisoformat(settings.pricing_checked_on)
                ).days
            except ValueError:
                raise ModelError("reliable_rates_required") from None
            if age < 0 or age > 1:
                raise ModelError("rate_verification_expired")
        reserve = (
            cost(request.context_window, request.max_output_tokens, settings)
            if channel == "deepseek"
            else Decimal("0")
        )
        call_id = str(uuid4())
        with Session(self.engine) as s, s.begin():
            s.execute(text("SELECT pg_advisory_xact_lock(2026100404)"))
            if channel == "deepseek":
                total = s.scalar(select(func.coalesce(func.sum(Call.charged + Call.reserved), 0)))
                if Decimal(str(total)) + reserve > CAP:
                    raise ModelError("billing_cap_exceeded")
            s.add(
                Call(
                    id=call_id,
                    scope=scope,
                    channel=channel,
                    model_id=model_id,
                    input_hash=hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
                    status="running",
                    reserved=reserve,
                    charged=Decimal("0"),
                    usage={},
                    error=None,
                    created=datetime.now(UTC),
                    ended=None,
                )
            )
        return call_id

    def finish(
        self, call_id: str, usage: Usage | None, settings: Settings, error: str | None = None
    ) -> Decimal:
        with Session(self.engine) as s, s.begin():
            s.execute(text("SELECT pg_advisory_xact_lock(2026100404)"))
            row = s.scalar(select(Call).where(Call.id == call_id).with_for_update())
            if row is None:
                raise ModelError("model_call_missing")
            if row.ended is not None:
                return row.charged
            amount = Decimal("0")
            if row.channel == "deepseek":
                amount = row.reserved
                if (
                    usage
                    and usage.source == "provider"
                    and usage.input_tokens is not None
                    and usage.output_tokens is not None
                ):
                    amount = cost(usage.input_tokens, usage.output_tokens, settings)
            row.charged, row.reserved = amount, Decimal("0")
            row.usage = usage.model_dump() if usage else {"source": "unknown"}
            row.error, row.ended = error, datetime.now(UTC)
            row.status = "failed" if error else "completed"
            return amount

    def status(self, scope: str) -> dict[str, Any]:
        with Session(self.engine) as s:
            total = s.scalar(select(func.coalesce(func.sum(Call.charged + Call.reserved), 0)))
            calls = s.scalars(
                select(Call).where(Call.scope == scope).order_by(Call.created.desc()).limit(20)
            ).all()
            return {
                "cap_cny": str(CAP),
                "conservative_used_or_reserved_cny": str(total),
                "calls": [
                    {
                        "id": r.id,
                        "channel": r.channel,
                        "model_id": r.model_id,
                        "status": r.status,
                        "usage": r.usage,
                        "estimated_cny": str(r.charged) if r.channel == "deepseek" else None,
                        "billing_kind": "configured_cny_upper_estimate"
                        if r.channel == "deepseek"
                        else "subscription"
                        if r.channel == "chatgpt"
                        else "local_unbilled",
                        "error": r.error,
                        "created": r.created.isoformat(),
                    }
                    for r in calls
                ],
            }
