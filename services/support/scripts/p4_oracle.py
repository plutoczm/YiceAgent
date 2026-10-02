"""Independent Python oracle over persisted business rows in the owned test schema."""

import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from p3_test_database import validate_schema
from sqlalchemy import create_engine, select, text

from app.demo.fixtures import validate_url
from app.store import OrderRow, PendingActionRow, TicketRow

ROOT = Path(__file__).resolve().parents[3]


def oracle(as_of):
    state = json.loads((ROOT / ".runtime/demo-processes.json").read_text())
    config = json.loads(Path(state["config"]).read_text())
    schema = config["test_schema"]
    db = json.loads((ROOT / ".runtime/demo-db.json").read_text())
    validate_url(db["database_url"])
    engine = create_engine(db["database_url"])
    validate_schema(engine, schema)
    engine.dispose()
    engine = create_engine(db["database_url"], connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT owner FROM workflowops_demo_owner")).scalar()
                == "workflowops-demo"
            )
            orders = (
                connection.execute(select(OrderRow).where(OrderRow.tenant_id == "tenant-a"))
                .mappings()
                .all()
            )
            actions = (
                connection.execute(
                    select(PendingActionRow).where(PendingActionRow.tenant_id == "tenant-a")
                )
                .mappings()
                .all()
            )
            tickets = (
                connection.execute(select(TicketRow).where(TicketRow.tenant_id == "tenant-a"))
                .mappings()
                .all()
            )
        return {
            "orders_total": [[len(orders)]],
            "refund_requests_by_status": [
                list(v)
                for v in sorted(
                    Counter(a["status"] for a in actions if a["kind"] == "refund").items()
                )
            ],
            "pending_approval_count": [[sum(a["status"] == "pending_approval" for a in actions)]],
            "tickets_by_status": [
                list(v) for v in sorted(Counter(t["status"] for t in tickets).items())
            ],
            "overdue_open_tickets": [
                [
                    sum(
                        t["status"] not in {"resolved", "closed"}
                        and t["sla_due_at"].replace(tzinfo=UTC) < as_of
                        for t in tickets
                    )
                ]
            ],
        }
    finally:
        engine.dispose()


if __name__ == "__main__":
    print(
        json.dumps(
            oracle(datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))), sort_keys=True
        )
    )
