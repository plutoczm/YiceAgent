"""Generate synthetic inputs only; never connect to an operational database."""

import argparse
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERSION = "workflowops-fixture-1.0"
REFERENCE_TIME = "2026-10-01T00:00:00Z"


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def generate(seed=42, reference_time=REFERENCE_TIME):
    rng = random.Random(seed)
    customers, orders = [], []
    for t, tenant in enumerate(("tenant-a", "tenant-b"), 1):
        for c in range(1, 4):
            customer = f"customer-{'a' if t == 1 else 'b'}{c}"
            customers.append({"tenant_id": tenant, "customer_id": customer})
            for j, amount in enumerate(
                (199, 899, 89, 500 if c == 1 else rng.randint(100, 499))
            ):
                orders.append(
                    {
                        "tenant_id": tenant,
                        "customer_id": customer,
                        "order_id": f"ORD-{t * 1000 + (c - 1) * 4 + j + 1}",
                        "status": "shipped" if j == 2 else "delivered",
                        "amount": amount,
                        "refundable_amount": 0 if j == 2 else amount,
                        "refund_status": "none",
                        "return_status": "none",
                    }
                )
    scenarios = json.loads(
        (ROOT / "demo/scenarios/default.json").read_text(encoding="utf-8")
    )
    return {
        "fixture_version": "v1",
        "generator_version": VERSION,
        "seed": seed,
        "reference_time": reference_time,
        "currency": "CNY",
        "synthetic": True,
        "tenants": ["tenant-a", "tenant-b"],
        "customers": customers,
        "orders": orders,
        "scenarios": scenarios,
    }


def write_fixture(output, seed=42, reference_time=REFERENCE_TIME):
    output.mkdir(parents=True, exist_ok=True)
    payload = canonical(generate(seed, reference_time))
    manifest = {
        "fixture_version": "v1",
        "generator_version": VERSION,
        "fixture_sha256": hashlib.sha256(payload).hexdigest(),
        "schema_sha256": hashlib.sha256(
            (ROOT / "contracts/demo_fixture/v1/schema.json").read_bytes()
        ).hexdigest(),
        "byte_count": len(payload),
        "order_count": 24,
    }
    for name, data in (
        ("fixture.json", payload),
        ("fixture-manifest.json", canonical(manifest)),
    ):
        path = output / name
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reference-time", default=REFERENCE_TIME)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_fixture(args.output, args.seed, args.reference_time)))
