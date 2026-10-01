"""数仓设计方案的结构化打分。

不依赖语义评估：JSON 可解析 → 必需键齐全 → 分层覆盖（ods/dwd/dws/ads 非空）
→ 表名模式 ^(ods|dwd|dws|ads|dim|fact)_ → DDL 存在 → 指标非空，逐条通过率。
"""

from __future__ import annotations

import json
import re
from typing import Any

REQUIRED_KEYS = (
    "source_tables", "ods", "dwd", "dws", "ads", "dim",
    "fact_tables", "relationships", "ddl", "metrics", "recommendations",
)
LAYERS = ("ods", "dwd", "dws", "ads")
TABLE_NAME_PATTERN = re.compile(r"^(ods|dwd|dws|ads|dim|fact)_", re.IGNORECASE)


def _extract_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    payload = _parse_longest_json_prefix(stripped)
    if not isinstance(payload, dict):
        raise ValueError("design JSON must be an object")
    return payload


def _parse_longest_json_prefix(content: str) -> Any:
    """解析最长有效 JSON 前缀，容忍长输出被 max_new_tokens 截断。"""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 从尾部往回逐步截断，找到最长可解析前缀（含最外层对象闭合）。
    # 二分/线性截断：优先在逗号/冒号处截断，保持结构可恢复。
    depth = 0
    in_string = False
    escape = False
    end_index = -1
    for index, char in enumerate(content):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                end_index = index
                break
    if end_index > 0:
        candidate = content[: end_index + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # 兜底：逐步缩短扫描
    for end in range(len(content), 0, -1):
        try:
            return json.loads(content[:end])
        except json.JSONDecodeError:
            continue
    raise ValueError("no valid JSON found")


def score_warehouse_case(*, predicted: str) -> dict[str, Any]:
    criteria: dict[str, bool] = {}
    payload: dict[str, Any] | None = None
    json_valid = False
    try:
        payload = _extract_json(predicted)
        json_valid = True
    except (json.JSONDecodeError, ValueError):
        payload = None

    if payload is not None:
        for key in REQUIRED_KEYS:
            criteria[f"key_{key}"] = key in payload and payload.get(key) is not None
        for layer in LAYERS:
            criteria[f"layer_{layer}"] = len(payload.get(layer) or []) > 0
        named_tables = [
            table.get("name", "")
            for table in payload.get("ods", []) + payload.get("dwd", [])
            + payload.get("dws", []) + payload.get("ads", [])
            + payload.get("dim", []) + payload.get("fact_tables", [])
            if isinstance(table, dict)
        ]
        criteria["table_name_pattern"] = (
            bool(named_tables) and all(TABLE_NAME_PATTERN.match(name) for name in named_tables)
        )
        criteria["ddl_present"] = len(payload.get("ddl") or []) > 0
        criteria["metrics_present"] = len(payload.get("metrics") or []) > 0
    else:
        # JSON 被截断（长输出超 max_new_tokens）时，按原文 key 存在性做宽容评分
        criteria["json_valid"] = False
        for key in REQUIRED_KEYS:
            criteria[f"key_{key}"] = f'"{key}"' in predicted
        for layer in LAYERS:
            criteria[f"layer_{layer}"] = f'"{layer}"' in predicted
        criteria["table_name_pattern"] = bool(TABLE_NAME_PATTERN.search(predicted))
        criteria["ddl_present"] = '"ddl"' in predicted
        criteria["metrics_present"] = '"metrics"' in predicted

    passed = sum(1 for value in criteria.values() if value)
    return {
        "json_valid": json_valid,
        "criteria": criteria,
        "score": round(passed / len(criteria), 4),
    }


def aggregate_warehouse(results: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(results)
    if count == 0:
        return {"cases": 0, "json_valid_rate": 0.0, "avg_score": 0.0}
    json_valid_rate = sum(1 for r in results if r["json_valid"]) / count
    avg_score = sum(r["score"] for r in results) / count
    return {
        "cases": count,
        "json_valid_rate": round(json_valid_rate, 4),
        "avg_score": round(avg_score, 4),
    }
