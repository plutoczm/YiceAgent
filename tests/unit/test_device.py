"""Device policy protects foreign workloads and explicitly reports CPU fallback."""

import pytest

from yice_agent.device import MIN_FREE_VRAM_BYTES, Accelerator, DeviceUnavailable, choose_device


def available(**values):
    data = {
        "cuda_available": True,
        "kernel_ready": True,
        "free_bytes": 6 * 1024**3,
        "total_bytes": 8 * 1024**3,
    }
    data.update(values)
    return Accelerator(**data)


def test_auto_uses_compatible_available_gpu():
    result = choose_device("auto", available())
    assert result.device == "cuda:0" and result.reason == "gpu_available"


@pytest.mark.parametrize(
    "values,reason",
    [
        ({"cuda_available": False}, "cuda_unavailable"),
        ({"kernel_ready": False}, "cuda_kernel_unavailable"),
        ({"free_bytes": MIN_FREE_VRAM_BYTES - 1}, "insufficient_free_vram"),
    ],
)
def test_auto_reports_why_gpu_cannot_be_used(values, reason):
    result = choose_device("auto", available(**values))
    assert result.device == "cpu" and result.reason == reason
    with pytest.raises(DeviceUnavailable, match=reason):
        choose_device("cuda:0", available(**values))


def test_explicit_cpu_is_respected():
    assert choose_device("cpu", available()).reason == "explicit_cpu"


def test_threshold_is_inclusive():
    assert choose_device("auto", available(free_bytes=MIN_FREE_VRAM_BYTES)).device == "cuda:0"


def test_auto_gpu_oom_retries_read_only_once_on_cpu(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    from yice_agent.device import run_read_only_inference

    class OOM(RuntimeError):
        pass

    events = []
    fake = ModuleType("torch")
    fake.cuda = SimpleNamespace(
        OutOfMemoryError=OOM, empty_cache=lambda: events.append("clear_owned_cache")
    )
    monkeypatch.setitem(sys.modules, "torch", fake)

    def operation(device):
        events.append(device)
        if device == "cuda:0":
            raise OOM()
        return "result"

    result, choice = run_read_only_inference(operation, "auto", available())
    assert result == "result" and choice.reason == "gpu_out_of_memory"
    assert events == ["cuda:0", "clear_owned_cache", "cpu"]


def test_forced_gpu_oom_does_not_hide_failure(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    from yice_agent.device import run_read_only_inference

    class OOM(RuntimeError):
        pass

    fake = ModuleType("torch")
    fake.cuda = SimpleNamespace(OutOfMemoryError=OOM)
    monkeypatch.setitem(sys.modules, "torch", fake)

    def operation(device):
        raise OOM()

    with pytest.raises(OOM):
        run_read_only_inference(operation, "cuda:0", available())


def test_default_settings_gpu_preference():
    from pydantic import ValidationError

    from yice_agent.config import Settings

    value = Settings(database_password="d" * 48, qdrant_key="q" * 48)
    assert value.inference_device == "auto"
    with pytest.raises(ValidationError):
        Settings(database_password="d" * 48, qdrant_key="q" * 48, inference_device="cuda:9")


def test_model_coordinator_does_not_hold_a_cuda_context(monkeypatch):
    """The coordinator must not compete with the isolated model children."""
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "accelerator_cli",
        Path(__file__).resolve().parents[2] / "scripts/models/verify_accelerator.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(
        module, "safe_report", lambda *args: pytest.fail("Coordinator initialized CUDA")
    )
    monkeypatch.setattr(sys, "argv", ["verify_v2_accelerator.py", "--models", "--device", "auto"])
    assert module.main() == 0
    assert len(calls) == 2
    assert all("--model" in command and "--device" in command for command in calls)
