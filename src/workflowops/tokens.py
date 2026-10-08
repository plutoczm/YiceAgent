"""One explicit cross-provider estimator. These are not native provider token counts."""

import json
import math
from functools import lru_cache


@lru_cache(maxsize=1)
def encoding():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_text(value: str, factor: float = 1.2) -> int:
    try:
        raw = len(encoding().encode(value, disallowed_special=()))
    except Exception:
        # Safe upper bound when cached tokenizer cannot be loaded; never called exact.
        raw = len(value.encode("utf-8"))
    return math.ceil(raw * max(1.2, factor))


def estimate(request, factor: float = 1.2) -> dict:
    # Includes normalized envelope, tool definitions and output schema.
    value = request.model_dump(mode="json")
    value.pop("timeout_seconds", None)
    value.pop("context_window", None)
    value.pop("max_output_tokens", None)
    serial = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    try:
        raw = len(encoding().encode(serial, disallowed_special=()))
        source = "cl100k_base_cross_provider_estimate"
    except Exception:
        raw = len(serial.encode("utf-8"))
        source = "utf8_upper_bound_estimate"
    return {
        "input_tokens": math.ceil(raw * max(1.2, factor)),
        "base_tokens": raw,
        "factor": max(1.2, factor),
        "source": source,
        "exact_for_provider": False,
    }
