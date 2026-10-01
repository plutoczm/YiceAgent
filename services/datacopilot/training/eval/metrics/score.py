"""评测汇总：生成报告字典与控制台对比表，不预设任何目标值。"""

from __future__ import annotations

from pathlib import Path

from training.eval.metrics.text2sql_metrics import aggregate_text2sql
from training.eval.metrics.warehouse_metrics import aggregate_warehouse


def summarize(
    *,
    text2sql_results: list[dict],
    warehouse_results: list[dict],
) -> dict:
    return {
        "text2sql": aggregate_text2sql(text2sql_results),
        "warehouse_design": aggregate_warehouse(warehouse_results),
    }


def write_report(report: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        __import__("json").dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def render_summary_markdown(base_report: dict, ft_report: dict) -> str:
    lines = [
        "# 微调前后评测对比",
        "",
        "> 数值为实际运行结果，如实记录，不预设目标。",
        "",
        "| 指标 | 基座模型 | 微调模型 | Δ |",
        "| --- | --- | --- | --- |",
    ]
    for task in ("text2sql", "warehouse_design"):
        for metric, label in _metric_labels(task):
            base_value = base_report[task].get(metric, 0.0)
            ft_value = ft_report[task].get(metric, 0.0)
            delta = ft_value - base_value
            lines.append(
                f"| {label} | {base_value} | {ft_value} | {delta:+.4f} |"
            )
    return "\n".join(lines) + "\n"


def _metric_labels(task: str) -> list[tuple[str, str]]:
    if task == "text2sql":
        return [
            ("valid_sql_rate", "Text2SQL 合法率"),
            ("component_f1", "Text2SQL 组件 F1"),
            ("exact_match_rate", "Text2SQL 精确匹配率"),
        ]
    return [
        ("json_valid_rate", "数仓 JSON 合法率"),
        ("avg_score", "数仓结构评分"),
    ]
