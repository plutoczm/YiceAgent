"""Business provision refuses foreign state; replay uses actual domain services."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from workflowops.business.bootstrap import provision
from workflowops.business.models import Version
from workflowops.business.rules import BusinessError
from workflowops.business.scenarios import replay

pytestmark = pytest.mark.integration


def test_replay_runs_both_real_business_families(business):
    service, _ = business
    first = replay(service)
    second = replay(service)
    assert first["run_id"] != second["run_id"]
    assert len(first["scenarios"]) == 4 and len(second["scenarios"]) == 4
    assert first["model_calls"] == second["model_calls"] == 0
    ids = {row["order"]["order_id"] for row in first["scenarios"]}
    assert ids.isdisjoint({row["order"]["order_id"] for row in second["scenarios"]})
    for result in first["scenarios"]:
        assert result["order"]["refund_status"] == "refunded"


def test_provision_rejects_unknown_tables_and_marker_drift(business):
    service, schema = business
    with service.engine.begin() as connection:
        connection.execute(text("CREATE TABLE unrelated_user_data (id integer)"))
    with pytest.raises(BusinessError, match="unexpected_business_tables"):
        provision(service.engine, schema)
    with service.engine.begin() as connection:
        connection.execute(text("DROP TABLE unrelated_user_data"))
    with Session(service.engine) as session, session.begin():
        session.get(Version, "business").version = 99
    with pytest.raises(BusinessError, match="business_schema_version_invalid"):
        provision(service.engine, schema)


def test_search_path_input_cannot_target_v1(business):
    service, _ = business
    with pytest.raises(BusinessError, match="business_schema_not_owned"):
        provision(service.engine, "workflowops_demo")
