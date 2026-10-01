"""Black-box HTTP and isolated-service oracle helpers."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def config():
    state = json.loads((ROOT / ".runtime/demo-processes.json").read_text())
    path = Path(state["config"])
    value = json.loads(path.read_text())
    assert value.get("test_schema", "").startswith("p3_test_")
    assert Path(value["delivery_root"]).is_relative_to(path.parent)
    return value, path.parent


def select(client, persona):
    assert (
        client.post("/api/demo/session", json={"persona": persona}).status_code == 200
    )


def run(client, workflow, payload, evidence):
    response = client.post(f"/api/workflows/{workflow}/runs", json={"payload": payload})
    assert response.status_code == 200
    result = response.json()
    evidence.append(
        {key: result[key] for key in ("run_id", "workflow_id", "status", "domain_refs")}
    )
    return result


def oracle(as_of):
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from dev import service_process

    result = service_process(
        "supportops", ["scripts/p4_oracle.py", as_of], capture=True
    )
    assert result.returncode == 0, "isolated read-only oracle failed"
    return json.loads(result.stdout.strip().splitlines()[-1])


def query_pinned(config, export_id):
    """Independent DataCopilot process checks an old directory through D1-D4."""
    import os
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from dev import service_process

    # Fixed test entrypoint receives server-generated identifiers, never arbitrary SQL.
    env_key = "WORKFLOWOPS_P4_PINNED"
    old = os.environ.get(env_key)
    os.environ[env_key] = json.dumps(
        {"root": config["delivery_root"], "export_id": export_id}
    )
    try:
        result = service_process(
            "datacopilot", ["scripts/p4_query_pinned.py"], capture=True
        )
        assert result.returncode == 0
        return json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old
