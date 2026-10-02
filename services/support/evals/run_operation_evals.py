"""Stateful S2b HTTP regression; PostgreSQL concurrency is verified separately."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.main import create_app
from app.store import OrderRow

SCENARIOS = (
    "normal_confirm",
    "high_pending_approval",
    "approved_high_submission",
    "rejected_high_request",
    "duplicate_preparation",
    "cross_tenant_operation",
    "stale_authorization",
)


def run_scenario(name: str, key) -> None:
    with TemporaryDirectory(prefix="supportops-operation-e2e-") as tmp, ExitStack() as cleanup:
        settings = Settings(
            database_url=f"sqlite:///{Path(tmp) / 'operations.db'}",
            auth_issuer="https://identity.example",
            auth_audience="supportops",
            auth_jwks_url="https://identity.example/jwks",
        )
        c = build_container(settings)
        cleanup.callback(c.store.engine.dispose)
        cleanup.callback(c.observability.shutdown)
        c.store.seed_demo_data(tenant_id="TENANT-A")
        c.auth._jwks_client = SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
        )
        high = name in {
            "high_pending_approval",
            "approved_high_submission",
            "rejected_high_request",
        }
        customer_id = "CUST-002" if high else "CUST-001"
        order_id = "ORD-2001" if high else "ORD-1001"

        def headers(*, admin=False, tenant="TENANT-A"):
            now = int(time())
            token = jwt.encode(
                {
                    "sub": "admin" if admin else "customer",
                    "tenant_id": tenant,
                    "customer_id": None if admin else customer_id,
                    "roles": ["support_admin"] if admin else ["customer"],
                    "iss": settings.auth_issuer,
                    "aud": settings.auth_audience,
                    "iat": now,
                    "exp": now + 300,
                },
                key,
                algorithm="RS256",
            )
            return {"Authorization": "Bearer " + token}

        with TestClient(create_app(c)) as client:

            def prepare():
                response = client.post(
                    "/v1/support/messages",
                    headers=headers(),
                    json={"conversation_id": name, "message": "refund " + order_id},
                )
                assert response.status_code == 200
                return response.json()

            first = prepare()
            op = first["operation_id"]
            assert op

            def confirm(value=True):
                response = client.post(
                    f"/v1/actions/{op}/confirm", headers=headers(), json={"confirm": value}
                )
                assert response.status_code == 200
                return response.json()

            def state():
                response = client.get(f"/v1/operations/{op}", headers=headers())
                assert response.status_code == 200
                return response.json()

            if name == "normal_confirm":
                assert confirm()["operation_status"] == "submitted"
                assert confirm()["result"] == state()["result"]
            elif name == "high_pending_approval":
                assert first["handoff"] and first["pending_action_id"] is None
                assert state()["status"] == "pending_approval"
                assert confirm()["operation_status"] == "pending_approval"
                assert state()["customer_confirmed"] and not state()["human_approved"]
            elif name in {"approved_high_submission", "rejected_high_request"}:
                assert confirm()["operation_status"] == "pending_approval"
                outcome = client.post(
                    f"/v1/agent/operations/{op}/decision",
                    headers=headers(admin=True),
                    json={
                        "decision": "approve" if name == "approved_high_submission" else "reject",
                        "expected_version": state()["version"],
                    },
                )
                assert outcome.status_code == 200
                expected = "submitted" if name == "approved_high_submission" else "rejected"
                assert outcome.json()["status"] == expected
                assert confirm()["operation_status"] == expected
                if expected == "rejected":
                    assert prepare()["operation_id"] != op
            elif name == "duplicate_preparation":
                assert prepare()["operation_id"] == op
                assert confirm()["operation_status"] == "submitted"
                assert prepare()["operation_id"] == op
            elif name == "cross_tenant_operation":
                assert (
                    client.get(
                        f"/v1/operations/{op}", headers=headers(tenant="TENANT-B")
                    ).status_code
                    == 404
                )
                assert (
                    client.post(
                        f"/v1/actions/{op}/confirm",
                        headers=headers(tenant="TENANT-B"),
                        json={"confirm": True},
                    ).status_code
                    == 404
                )
                assert (
                    client.post(
                        f"/v1/agent/operations/{op}/decision",
                        headers=headers(admin=True, tenant="TENANT-B"),
                        json={"decision": "approve", "expected_version": 1},
                    ).status_code
                    == 404
                )
            else:
                with c.store._session.begin() as session:
                    session.get(OrderRow, order_id).refundable_amount = 899
                assert confirm()["operation_status"] == "invalidated"
                assert confirm()["result"] == {"reason": "refund_proposal_changed"}
            actual = c.store.get_order(order_id, customer_id, tenant_id="TENANT-A")
            should_submit = name in {
                "normal_confirm",
                "approved_high_submission",
                "duplicate_preparation",
            }
            assert actual.refund_status == ("requested" if should_submit else "none")


def main() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    failures = []
    for name in SCENARIOS:
        try:
            run_scenario(name, key)
        except AssertionError as exc:
            failures.append({"scenario": name, "reason": str(exc)})
    print(
        json.dumps(
            {
                "benchmark_version": "supportops-operation-regression-s2b",
                "scope": (
                    "local SQLite + verified JWT HTTP flows; not a PostgreSQL concurrency claim"
                ),
                "scenarios": len(SCENARIOS),
                "passed": len(SCENARIOS) - len(failures),
                "failures": failures,
            },
            indent=2,
        )
    )
    if failures:
        raise SystemExit("operation HTTP regression failed")


if __name__ == "__main__":
    main()
