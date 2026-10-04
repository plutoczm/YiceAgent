"""GPU-first policy for the isolated inference process, without stopping other workloads."""

import importlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

Preference = Literal["auto", "cpu", "cuda:0"]
MIN_FREE_VRAM_BYTES = 2560 * 1024 * 1024


@dataclass(frozen=True)
class Accelerator:
    cuda_available: bool
    kernel_ready: bool
    free_bytes: int = 0
    total_bytes: int = 0
    name: str | None = None
    torch_version: str | None = None
    cuda_build: str | None = None


@dataclass(frozen=True)
class DeviceChoice:
    device: Literal["cpu", "cuda:0"]
    reason: str
    free_bytes: int


class DeviceUnavailable(RuntimeError):
    pass


def choose_device(preference: Preference, accelerator: Accelerator) -> DeviceChoice:
    if preference == "cpu":
        return DeviceChoice("cpu", "explicit_cpu", accelerator.free_bytes)
    if not accelerator.cuda_available:
        reason = "cuda_unavailable"
    elif not accelerator.kernel_ready:
        reason = "cuda_kernel_unavailable"
    elif accelerator.free_bytes < MIN_FREE_VRAM_BYTES:
        reason = "insufficient_free_vram"
    else:
        return DeviceChoice("cuda:0", "gpu_available", accelerator.free_bytes)
    if preference == "cuda:0":
        raise DeviceUnavailable(reason)
    return DeviceChoice("cpu", reason, accelerator.free_bytes)


def probe_accelerator() -> Accelerator:
    """Execute a tiny CUDA kernel rather than treating nvidia-smi as proof."""
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        return Accelerator(False, False)
    if not torch.cuda.is_available():
        return Accelerator(
            False, False, torch_version=str(torch.__version__), cuda_build=torch.version.cuda
        )
    ready = False
    try:
        tensor = torch.ones((16, 16), device="cuda:0")
        result = tensor @ tensor
        torch.cuda.synchronize()
        ready = bool(torch.all(result == 16).item())
        del tensor, result
        torch.cuda.empty_cache()
    except (RuntimeError, AssertionError):
        ready = False
    try:
        free, total = torch.cuda.mem_get_info(0)
        name = torch.cuda.get_device_name(0)
    except RuntimeError:
        return Accelerator(
            True, False, torch_version=str(torch.__version__), cuda_build=torch.version.cuda
        )
    return Accelerator(
        True, ready, int(free), int(total), name, str(torch.__version__), torch.version.cuda
    )


def safe_report(preference: Preference = "auto") -> dict[str, object]:
    accelerator = probe_accelerator()
    choice = choose_device(preference, accelerator)
    return {
        "preference": preference,
        "accelerator": asdict(accelerator),
        "selection": asdict(choice),
    }


def run_read_only_inference[Result](
    operation: Callable[[Literal["cpu", "cuda:0"]], Result],
    preference: Preference = "auto",
    accelerator: Accelerator | None = None,
) -> tuple[Result, DeviceChoice]:
    """Only embedding/reranking may use this fallback; never replay business writes."""
    torch = importlib.import_module("torch")
    choice = choose_device(preference, accelerator or probe_accelerator())
    if choice.device == "cpu":
        return operation("cpu"), choice
    try:
        return operation("cuda:0"), choice
    except torch.cuda.OutOfMemoryError:
        if preference != "auto":
            raise
    # Outside the exception block: the traceback no longer retains failed model tensors.
    torch.cuda.empty_cache()
    return operation("cpu"), DeviceChoice("cpu", "gpu_out_of_memory", choice.free_bytes)
