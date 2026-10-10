"""Existing network settings affect only Node children; no secret or user-level writes."""

import pytest

from yice_agent.models import node_env


@pytest.mark.parametrize(
    "server",
    [
        "remote.example:7890",
        "http://user:private@127.0.0.1:7890",
        "127.0.0.1:7890/private",
        "127.0.0.1:7890?private=value",
        "socks5://127.0.0.1:7890",
        "http=127.0.0.1:7890",
        "127.0.0.1:999999",
    ],
)
def test_windows_proxy_is_only_unauthenticated_configured_https_loopback(server):
    assert node_env.local_proxy(server) is None


def test_existing_windows_proxy_only_changes_child_environment(monkeypatch):
    monkeypatch.setattr(node_env, "windows_proxy", lambda: "http://127.0.0.1:7890")
    parent = {
        "PATH": "owned-path",
        "OPENAI_API_KEY": "SYNTHETIC_OTHER_TOOL_SECRET",
        "DEEPSEEK_API_KEY": "SYNTHETIC_OTHER_TOOL_SECRET",
        "PI_CODING_AGENT_DIR": "other-tool-dir",
        "NO_PROXY": "internal.example",
    }
    child = node_env.node_environment(parent)
    assert child["PATH"] == "owned-path"
    assert child["HTTPS_PROXY"] == child["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert child["NODE_USE_ENV_PROXY"] == "1"
    assert {"localhost", "127.0.0.1", "::1"} <= set(child["NO_PROXY"].split(","))
    assert all(
        k not in child for k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "PI_CODING_AGENT_DIR")
    )
    assert "HTTPS_PROXY" not in parent and "OPENAI_API_KEY" in parent


def test_explicit_process_configuration_has_priority(monkeypatch):
    def unexpected_registry_read():
        raise AssertionError("explicit_proxy_must_not_read_windows_setting")

    monkeypatch.setattr(node_env, "windows_proxy", unexpected_registry_read)
    child = node_env.node_environment({"HTTPS_PROXY": "http://127.0.0.1:9000"})
    assert child["HTTPS_PROXY"] == "http://127.0.0.1:9000"
    assert child["NODE_USE_ENV_PROXY"] == "1"
    assert node_env.node_environment({"NODE_USE_ENV_PROXY": "0"}) == {"NODE_USE_ENV_PROXY": "0"}


def test_per_protocol_proxy_uses_https_entry():
    assert (
        node_env.local_proxy("http=127.0.0.1:7000;https=127.0.0.1:7890") == "http://127.0.0.1:7890"
    )
    assert node_env.local_proxy("[::1]:7890") == "http://[::1]:7890"


def test_no_existing_proxy_preserves_direct_networking(monkeypatch):
    monkeypatch.setattr(node_env, "windows_proxy", lambda: None)
    assert node_env.node_environment({"PATH": "owned-path"}) == {"PATH": "owned-path"}
