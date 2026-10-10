"""Reproducible domain scenarios: actual writes with a fresh replay ID, no database reset."""

from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from yice_agent.business.contracts import DRAFT, Approval, Confirmation, SimulationEvent
from yice_agent.business.service import BusinessService
from yice_agent.identity import PERSONAS


def replay(service: BusinessService) -> dict[str, Any]:
    customer = PERSONAS["customer-a1"]
    operator = PERSONAS["operator-a"]
    run_id = str(uuid4())
    sequence = 0

    def key() -> str:
        nonlocal sequence
        sequence += 1
        return str(uuid5(NAMESPACE_URL, "workflowops-v2-replay:" + run_id + ":" + str(sequence)))

    def propose(**values: Any) -> dict[str, Any]:
        return service.propose(
            customer, DRAFT.validate_python({"client_request_id": key(), **values})
        )

    def confirm(row: dict[str, Any]) -> dict[str, Any]:
        return service.confirm(
            customer,
            row["action_id"],
            Confirmation(client_request_id=key(), confirm=True, expected_version=row["version"]),
        )

    def simulate(order_id: str, event: str) -> dict[str, Any]:
        row = service.order(operator, order_id)
        request = SimulationEvent.model_validate(
            {
                "client_request_id": key(),
                "order_id": order_id,
                "expected_version": row["version"],
                "event": event,
            }
        )
        return service.simulate(operator, request)

    evidence = []
    for family, prefix, low, high in [
        ("ecommerce", "ECO", "CABLE", "PHONE"),
        ("food", "FOOD", "TEA", "FAMILY"),
    ]:
        order = confirm(
            propose(
                kind="place_order",
                order_type=family,
                items=[{"product_id": prefix + "-A-" + low, "quantity": 2}],
                note="可重放模拟业务",
            )
        )["result"]["order_id"]
        confirm(propose(kind="pay", order_id=order))
        simulate(order, "payment_succeeded")
        for _ in range(3 if family == "ecommerce" else 4):
            simulate(order, "advance")
        confirm(propose(kind="refund", order_id=order))
        simulate(order, "refund_succeeded")
        assert service.order(customer, order)["refunded_amount"] == "39.80"
        evidence.append(
            {"scenario": family + "_delivery_refund", "order": service.order(customer, order)}
        )
        order = confirm(
            propose(
                kind="place_order",
                order_type=family,
                items=[{"product_id": prefix + "-A-" + high, "quantity": 1}],
                note="审批与取消模拟业务",
            )
        )["result"]["order_id"]
        confirm(propose(kind="pay", order_id=order))
        simulate(order, "payment_succeeded")
        draft = propose(kind="cancel_order", order_id=order)
        assert draft["status"] == "pending_approval"
        approved = service.review(
            operator,
            draft["action_id"],
            Approval(
                client_request_id=key(), decision="approve", expected_version=draft["version"]
            ),
        )
        confirm(approved)
        simulate(order, "refund_succeeded")
        assert service.order(customer, order)["status"] == "cancelled"
        evidence.append(
            {
                "scenario": family + "_approved_cancellation",
                "order": service.order(customer, order),
                "action_id": draft["action_id"],
            }
        )
    return {
        "run_id": run_id,
        "scenarios": evidence,
        "source": "real_local_domain_simulator",
        "model_calls": 0,
    }
