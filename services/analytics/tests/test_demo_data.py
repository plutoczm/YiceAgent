import hashlib
import importlib.util
import json
import random
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "scripts/generate_demo_data.py"
SPEC = importlib.util.spec_from_file_location("demo_generator", PATH)
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def test_deterministic_bytes_and_manifest(tmp_path):
    state = random.getstate()
    first = generator.write_fixture(tmp_path / "first")
    second = generator.write_fixture(tmp_path / "second")
    assert first == second
    assert (tmp_path / "first/fixture.json").read_bytes() == (
        tmp_path / "second/fixture.json"
    ).read_bytes()
    assert random.getstate() == state
    assert (
        first["fixture_sha256"]
        == hashlib.sha256(
            (generator.ROOT / "demo/expected/fixture-v1.json").read_bytes()
        ).hexdigest()
    )


def test_distribution_and_business_inputs():
    fixture = generator.generate()
    assert len(fixture["orders"]) == 24
    assert len({o["order_id"] for o in fixture["orders"]}) == 24
    assert len(fixture["customers"]) == 6
    for customer in fixture["customers"]:
        assert (
            sum(
                o["tenant_id"] == customer["tenant_id"]
                and o["customer_id"] == customer["customer_id"]
                for o in fixture["orders"]
            )
            == 4
        )
    assert all(
        type(o["amount"]) is int and 0 <= o["refundable_amount"] <= o["amount"]
        for o in fixture["orders"]
    )
    assert all(
        o["refund_status"] == o["return_status"] == "none" for o in fixture["orders"]
    )
    assert not any(k in fixture for k in ("tickets", "actions", "receipts"))
    assert generator.canonical(generator.generate(43)) != generator.canonical(fixture)


def test_scenario_roles():
    fixture = generator.generate()
    orders = {o["order_id"]: o for o in fixture["orders"]}
    scenarios = fixture["scenarios"]
    assert [
        orders[scenarios[k]]["refundable_amount"]
        for k in ("low_refund", "high_refund", "non_refundable", "boundary_refund")
    ] == [199, 899, 0, 500]
    assert orders[scenarios["other_customer"]]["customer_id"] == "customer-a2"
    assert orders[scenarios["other_tenant"]]["tenant_id"] == "tenant-b"
    assert json.loads(generator.canonical(fixture)) == fixture
