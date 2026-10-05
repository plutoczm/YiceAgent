"""Real PostgreSQL budget serialization; no provider or charge is invoked."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from workflowops.config import Settings
from workflowops.contracts import GenerationRequest, Message, Usage
from workflowops.database import make_engine
from workflowops.model_store import Base, Call, ModelError, ModelStore
from workflowops.models.gateway import Gateway

pytestmark = pytest.mark.integration


@pytest.fixture
def ledger(business):
    _, schema = business
    control = make_engine(Settings.load(), schema)
    engine = control.execution_options(schema_translate_map={"workflowops_models": schema})
    with engine.begin() as c:
        Base.metadata.create_all(c)
    try:
        yield ModelStore(engine)
    finally:
        engine.dispose()
        control.dispose()


def priced():
    values = Settings.load().model_dump()
    values.update(
        database_password=Settings.load().database_password,
        qdrant_key=Settings.load().qdrant_key,
        deepseek_key=None,
        paid_calls_enabled=False,
        chatgpt_model=None,
        input_price_cny_per_million="2",
        output_price_cny_per_million="8",
        pricing_checked_on=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        pricing_source="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    )
    return Settings.model_validate(values)


def request():
    return GenerationRequest(
        messages=[Message(role="user", content="read-only synthetic budget test")]
    )


def test_reservation_unknown_usage_and_no_double_settlement(ledger):
    settings = priced()
    call_id = ledger.begin("tenant-a:unit", "deepseek", "deepseek-flash", request(), settings)
    expected = Decimal("0.028672")
    assert ledger.finish(call_id, None, settings, "provider_timeout") == expected
    assert (
        ledger.finish(call_id, Usage(input_tokens=1, output_tokens=1, source="provider"), settings)
        == expected
    )
    result = ledger.status("tenant-a:unit")
    assert (
        result["cap_cny"] == "15.00"
        and Decimal(result["conservative_used_or_reserved_cny"]) == expected
    )
    assert result["calls"][0]["usage"]["source"] == "unknown"
    assert ledger.status("tenant-b:other")["calls"] == []


def test_concurrent_reservations_cannot_exceed_fifteen(ledger):
    settings = priced()
    existing = ledger.begin("tenant-a:seed", "deepseek", "deepseek-flash", request(), settings)
    with Session(ledger.engine) as s, s.begin():
        row = s.get(Call, existing)
        row.reserved = Decimal("0")
        row.charged = Decimal("14.97")

    def reserve():
        try:
            return ledger.begin(
                "tenant-a:parallel", "deepseek", "deepseek-flash", request(), settings
            )
        except ModelError as error:
            assert str(error) == "billing_cap_exceeded"
            return None

    with ThreadPoolExecutor(2) as pool:
        calls = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(c is not None for c in calls) == 1
    assert Decimal(
        ledger.status("tenant-a:parallel")["conservative_used_or_reserved_cny"]
    ) <= Decimal("15")


def test_expired_rates_and_disabled_paid_channel_dispatch_nothing(ledger):
    settings = priced().model_copy(update={"pricing_checked_on": "2020-01-01"})
    with pytest.raises(ModelError, match="rate_verification_expired"):
        ledger.begin("tenant-a:unit", "deepseek", "deepseek-flash", request(), settings)
    gateway = Gateway(priced(), ledger)
    with pytest.raises(ModelError, match="paid_channel_not_enabled"):
        asyncio.run(gateway.generate("deepseek", request(), "tenant-a:unit"))
    assert ledger.status("tenant-a:unit")["calls"] == []


def test_chatgpt_subscription_never_claims_rmb_price(ledger):
    settings = priced()
    call_id = ledger.begin("tenant-a:unit", "chatgpt", "real-id-fixture", request(), settings)
    ledger.finish(call_id, Usage(input_tokens=100, output_tokens=10, source="provider"), settings)
    record = ledger.status("tenant-a:unit")["calls"][0]
    assert record["estimated_cny"] is None
    assert record["billing_kind"] == "subscription"
