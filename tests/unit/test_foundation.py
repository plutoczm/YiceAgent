"""Phase-one safety tests; do not claim external model or PostgreSQL validation."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yice_agent.config import Settings
from yice_agent.contracts import GenerationRequest, Message, RpcRequest, RpcResponse, ToolCall
from yice_agent.rpc import JsonlWorker, WorkerError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("v2_bootstrap", ROOT / "scripts/manage.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def configuration(**values):
    return Settings(database_password="p" * 48, qdrant_key="q" * 48, **values)


def test_no_secrets_in_repr_or_report():
    value = configuration(deepseek_key="SENSITIVE_KEY_VALUE")
    assert "SENSITIVE" not in repr(value)
    assert "p" * 48 not in repr(value)
    assert "SENSITIVE" not in json.dumps(value.safe_report())
    assert value.safe_report()["deepseek_configured"] is True


@pytest.mark.parametrize(
    "values",
    [
        {"owner": "workflowops-demo"},
        {"billing_cap_cny": "1000.00"},
        {"ollama_model": "unapproved"},
        {"database_url": "postgresql://elsewhere"},
        {"app_window": 32768},
        {"deepseek_model": "not-real"},
    ],
)
def test_configuration_rejects_scope_override(values):
    with pytest.raises(ValidationError):
        configuration(**values)


def test_configuration_path_is_fixed(tmp_path):
    with pytest.raises(ValueError, match="configuration_path_not_owned"):
        Settings.load(tmp_path / "settings.json")


def test_tool_group_complete_and_closed():
    call = ToolCall(id="call-1", name="order_lookup", arguments={"order_id": "E-1"})
    user = Message(role="user", content="查订单")
    assistant = Message(role="assistant", tool_calls=[call])
    tool = Message(role="tool", content="{}", tool_call_id="call-1")
    request = GenerationRequest(messages=[user, assistant, tool])
    assert len(request.messages) == 3
    with pytest.raises(ValidationError, match="incomplete_tool_group"):
        GenerationRequest(messages=[user, assistant])
    with pytest.raises(ValidationError, match="orphan_tool_result"):
        GenerationRequest(messages=[user, tool])
    with pytest.raises(ValidationError, match="incomplete_tool_group"):
        GenerationRequest(messages=[user, assistant, user])
    with pytest.raises(ValidationError):
        Message(role="user", content="x", tool_calls=[call])


def test_rpc_closed_response_and_ids():
    request = RpcRequest(method="health")
    result = RpcResponse.model_validate_json(
        json.dumps({"request_id": str(request.request_id), "result": {"ready": False}})
    )
    assert result.request_id == request.request_id
    with pytest.raises(ValidationError):
        RpcRequest(method="shell", payload={})
    with pytest.raises(ValidationError):
        RpcResponse.model_validate_json(
            json.dumps(
                {"request_id": str(uuid4()), "error": {"code": "failed"}, "result": "secret"}
            )
        )


def resource(service):
    name, volume, image, port, internal, destination = bootstrap.RESOURCES[service]
    return {
        "Name": "/" + name,
        "Config": {
            "Image": image,
            "Labels": {
                "com.workflowops.owner": "workflowops-v2",
                "com.docker.compose.project": "workflowops-v2",
            },
        },
        "HostConfig": {
            "PortBindings": {internal: [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}
        },
        "Mounts": [{"Name": volume, "Destination": destination}],
    }


@pytest.mark.parametrize("service", ["postgres", "qdrant"])
def test_owned_resource_boundaries(service):
    value = resource(service)
    bootstrap.validate_resource(value, service)
    value["Config"]["Labels"]["com.workflowops.owner"] = "workflowops-demo"
    with pytest.raises(ValueError, match="resource_owner_mismatch"):
        bootstrap.validate_resource(value, service)
    value = resource(service)
    internal = bootstrap.RESOURCES[service][4]
    value["HostConfig"]["PortBindings"][internal][0]["HostIp"] = "0.0.0.0"
    with pytest.raises(ValueError, match="container_binding_mismatch"):
        bootstrap.validate_resource(value, service)
    value = resource(service)
    value["Mounts"][0]["Name"] = "original_user_data"
    with pytest.raises(ValueError, match="container_mount_mismatch"):
        bootstrap.validate_resource(value, service)


def test_mutations_require_approval_before_any_action(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("external mutation reached before approval")

    monkeypatch.setattr(bootstrap, "inventory", forbidden)
    monkeypatch.setattr(bootstrap.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="explicit_approval_required"):
        bootstrap.install(False)
    with pytest.raises(ValueError, match="explicit_approval_required"):
        bootstrap.infra_up(False)


def test_jsonl_roundtrip(tmp_path):
    worker_file = tmp_path / "worker.py"
    worker_file.write_text(
        "import sys,json\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({'request_id':r['request_id'],'result':{'ready':False}}),flush=True)\n",
        encoding="utf-8",
    )

    async def run():
        worker = JsonlWorker([sys.executable, "-B", str(worker_file)])
        await worker.start()
        result = await worker.call(RpcRequest(method="health"))
        assert result == {"ready": False}
        await worker.close()
        assert worker.process is None

    asyncio.run(run())


def test_worker_timeout_stops_owned_process(tmp_path):
    path = tmp_path / "slow.py"
    path.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")

    async def run():
        worker = JsonlWorker([sys.executable, "-B", str(path)])
        await worker.start()
        process = worker.process
        with pytest.raises(TimeoutError):
            await worker.call(RpcRequest(method="health"), timeout=0.1)
        assert worker.process is None and process.returncode is not None

    asyncio.run(run())


def test_rpc_invalid_stdout(tmp_path):
    path = tmp_path / "invalid.py"
    path.write_text(
        "import sys\nfor line in sys.stdin: print('PRIVATE_SECRET_NOT_JSON',flush=True)\n",
        encoding="utf-8",
    )

    async def run():
        worker = JsonlWorker([sys.executable, "-B", str(path)])
        await worker.start()
        with pytest.raises(WorkerError, match="worker_protocol_invalid") as error:
            await worker.call(RpcRequest(method="health"))
        assert "PRIVATE_SECRET" not in str(error.value)
        assert worker.process is None

    asyncio.run(run())


def test_rpc_mismatched_id_terminates_worker(tmp_path):
    path = tmp_path / "mismatch.py"
    path.write_text(
        "import sys,json,uuid\nfor line in sys.stdin: print(json.dumps({'request_id':str(uuid.uuid4()),'result':{}}),flush=True)\n",
        encoding="utf-8",
    )

    async def run():
        worker = JsonlWorker([sys.executable, "-B", str(path)])
        await worker.start()
        with pytest.raises(WorkerError, match="worker_response_id_mismatch"):
            await worker.call(RpcRequest(method="health"))
        assert worker.process is None

    asyncio.run(run())


def test_reinstallation_uses_only_isolated_targets_and_no_user_registry_credentials(monkeypatch):
    calls = []

    def capture(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return None

    monkeypatch.setattr(bootstrap.subprocess, "run", capture)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "D:/nodejs/npm.CMD")
    monkeypatch.setenv("PIP_INDEX_URL", "https://PRIVATE_REGISTRY.example")
    bootstrap.install(True)
    assert calls
    for arguments, options in calls:
        env = options["env"]
        assert "PIP_INDEX_URL" not in env
        assert env["PIP_CONFIG_FILE"] == bootstrap.os.devnull
        assert env["NPM_CONFIG_USERCONFIG"] == bootstrap.os.devnull
        if "-m" in arguments and "pip" in arguments:
            target = Path(arguments[0]).resolve()
            assert target in {bootstrap.MAIN_PYTHON.resolve(), bootstrap.INFERENCE_PYTHON.resolve()}
