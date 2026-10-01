"""The integration runner cleans up even if schema preparation fails."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import p3_integration


@pytest.mark.parametrize("failure", ["exit", "exception"])
@pytest.mark.parametrize("restore", [False, True])
def test_failed_prepare_is_cleaned_and_prior_services_restored(
    monkeypatch, failure, restore
):
    calls = []
    monkeypatch.setattr(
        p3_integration, "STATE", SimpleNamespace(exists=lambda: restore)
    )
    monkeypatch.setattr(p3_integration, "down", lambda: calls.append("down"))
    monkeypatch.setattr(
        p3_integration, "up", lambda *args, **kwargs: calls.append("up")
    )
    monkeypatch.setattr(
        p3_integration.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("pytest started after failed preparation"),
    )

    def service(service, arguments):
        assert service == "supportops"
        action, schema = arguments[1:]
        calls.append((action, schema))
        if action == "create":
            if failure == "exception":
                raise RuntimeError("injected interrupted preparation")
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    if failure == "exception":
        with pytest.raises(RuntimeError, match="interrupted"):
            p3_integration.run(lambda port: True, {}, service)
    else:
        assert p3_integration.run(lambda port: True, {}, service) == 1
    actions = [call for call in calls if isinstance(call, tuple)]
    assert actions[0][0] == "create" and actions[1] == ("drop", actions[0][1])
    assert calls.count("up") == int(restore)
