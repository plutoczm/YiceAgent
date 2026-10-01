"""Real PG facts -> immutable export -> real HTTP -> governed DuckDB reports."""

import json
from pathlib import Path
from uuid import uuid4

import httpx2
from p4_helpers import ROOT, config, oracle, query_pinned, run, select


def test_business_change_refresh_and_five_independent_oracles():
    settings, directory = config()
    evidence = []
    with httpx2.Client(
        base_url="http://127.0.0.1:8010", trust_env=False, timeout=60
    ) as client:
        select(client, "operator-a")
        before_snapshot = run(
            client, "analytics_report", {"report_id": "orders_total"}, evidence
        )
        assert (
            before_snapshot["status"] == "failed" and before_snapshot["result"] is None
        )
        first = run(client, "refresh_snapshot", {}, evidence)
        assert first["status"] == "completed"
        index = Path(settings["delivery_root"]) / "tenant-a/current.json"
        first_index = json.loads(index.read_bytes())
        old_path = index.parent / first_index["export_id"]
        old_bytes = {p.name: p.read_bytes() for p in old_path.iterdir()}
        baseline = run(
            client,
            "analytics_report",
            {"report_id": "refund_requests_by_status"},
            evidence,
        )
        assert baseline["result"]["data"]["rows"] == []
        assert (
            baseline["domain_refs"]["delivery_fingerprint"]
            == first["domain_refs"]["delivery_fingerprint"]
        )
        select(client, "customer-a1")
        for workflow, payload in [
            ("refresh_snapshot", {}),
            ("analytics_report", {"report_id": "orders_total"}),
        ]:
            assert (
                run(client, workflow, payload, evidence)["error"]
                == "authorization_denied"
            )
        conversation = "p4-" + uuid4().hex
        low = run(
            client,
            "support_message",
            {
                "action": "refund",
                "order_id": "ORD-1001",
                "conversation_id": conversation,
            },
            evidence,
        )
        assert low["status"] == "waiting_confirmation"
        confirmed = run(
            client,
            "support_confirm",
            {"action_id": low["domain_refs"]["pending_action_id"], "confirm": True},
            evidence,
        )
        assert confirmed["result"]["operation_status"] == "submitted"
        high = run(
            client,
            "support_message",
            {
                "action": "refund",
                "order_id": "ORD-1002",
                "conversation_id": conversation + "h",
            },
            evidence,
        )
        assert high["status"] == "waiting_approval"
        human = run(
            client,
            "support_message",
            {
                "action": "handoff",
                "conversation_id": conversation + "t",
                "message": "P4 private human message",
            },
            evidence,
        )
        assert human["result"]["handoff"]
        select(client, "operator-a")
        stale = run(
            client,
            "analytics_report",
            {"report_id": "refund_requests_by_status"},
            evidence,
        )
        assert stale["result"]["data"]["rows"] == []
        second = run(client, "refresh_snapshot", {}, evidence)
        assert second["status"] == "completed"
        assert (
            second["domain_refs"]["snapshot_id"] != first["domain_refs"]["snapshot_id"]
        )
        assert old_bytes == {p.name: p.read_bytes() for p in old_path.iterdir()}
        observed = {}
        for report_id in (
            "orders_total",
            "refund_requests_by_status",
            "pending_approval_count",
            "tickets_by_status",
            "overdue_open_tickets",
        ):
            report = run(client, "analytics_report", {"report_id": report_id}, evidence)
            assert report["status"] == "completed", report
            view, data = report["result"], report["result"]["data"]
            expected = oracle(view["as_of"])
            assert data["rows"] == expected[report_id]
            assert data["row_count"] == len(expected[report_id])
            assert (
                data["snapshot"]["consistency"]
                == "postgresql_repeatable_read_read_only"
            )
            assert (
                data["source_plan_reauthorized"] and data["translated_sql_reauthorized"]
            )
            assert view["source"] == "template" and view["template_version"] == "v1"
            assert (
                report["domain_refs"]["delivery_fingerprint"]
                == second["domain_refs"]["delivery_fingerprint"]
            )
            assert str(ROOT) not in json.dumps(report) and "SELECT " not in json.dumps(
                report
            )
            observed[report_id] = data["rows"]
        assert observed["orders_total"] == [[12]]
        assert observed["pending_approval_count"] == [[1]]
        assert ["submitted", 1] in observed["refund_requests_by_status"]
        old_result = query_pinned(settings, first_index["export_id"])
        assert (
            old_result["rows"] == []
            and old_result["snapshot"]["snapshot_id"]
            == first["domain_refs"]["snapshot_id"]
        )
        # Refresh is independent: a publication path fault leaves committed business intact.
        saved = index.read_bytes()
        index.unlink()
        index.mkdir()
        try:
            failed = run(client, "refresh_snapshot", {}, evidence)
            assert failed["status"] == "failed"
        finally:
            index.rmdir()
            index.write_bytes(saved)
        assert run(
            client,
            "analytics_report",
            {"report_id": "pending_approval_count"},
            evidence,
        )["result"]["data"]["rows"] == [[1]]
        select(client, "customer-a1")
        operation = run(
            client,
            "support_lookup",
            {
                "kind": "operation",
                "object_id": confirmed["domain_refs"]["operation_id"],
            },
            evidence,
        )
        assert operation["result"]["status"] == "submitted"
    for item in evidence:
        contents = (ROOT / ".runtime/runs" / (item["run_id"] + ".jsonl")).read_text()
        assert (
            "P4 private human message" not in contents
            and '"rows"' not in contents
            and "SELECT " not in contents
        )
        assert not any(
            token in contents for token in settings["datacopilot_tokens"].values()
        )
    audit = (directory / "analytics-audit.jsonl").read_text()
    assert (
        "P4 private human message" not in audit
        and "SELECT " not in audit
        and '"rows"' not in audit
    )
    (ROOT / ".runtime/p4-reports-evidence.json").write_text(
        json.dumps({"runs": evidence, "observed": observed}, indent=2), encoding="utf-8"
    )
