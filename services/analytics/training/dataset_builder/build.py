"""数据集构建编排器。

用法（项目根目录）：
    python -m training.dataset_builder.build [--data-dir training/data] [--min-total 3200]

流程：四大能力构建器（Spider Text2SQL + SQL 审查 + 数仓设计 + 知识问答）
→ 去重 → 分层 90/10 划分 → train.jsonl / eval.jsonl / stats.json。

Spider 数据通过 HuggingFace hub（xlang/spider）或官方 zip 加载，失败时给出
明确提示；总量不足 --min-total 时构建失败，避免产出不完整数据集。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.dataset_builder.common.dedup import dedupe  # noqa: E402
from training.dataset_builder.common.records import (  # noqa: E402
    ShareGPTRecord,
    write_jsonl,
)
from training.dataset_builder.common.split import (  # noqa: E402
    counts_by_capability,
    sample_to,
    stratified_split,
)
from training.dataset_builder.knowledge_qa.generate import build_knowledge_records  # noqa: E402
from training.dataset_builder.sql_review.generate import build_sql_review_records  # noqa: E402
from training.dataset_builder.warehouse.generate import build_warehouse_records  # noqa: E402


# 每能力目标样本量（配比：Text2SQL 2000 / SQL 审查 500 / 数仓 500 / 知识 400）
CAPABILITY_TARGETS = {
    "text2sql": 2000,
    "sql_review": 500,
    "warehouse_design": 500,
    "knowledge_qa": 400,
}


def build_capability_records(
    knowledge_base_dir: Path,
    *,
    sql_review_target: int = 500,
) -> list[ShareGPTRecord]:
    records: list[ShareGPTRecord] = []
    records.extend(build_warehouse_records())
    records.extend(build_sql_review_records(target=sql_review_target))
    records.extend(build_knowledge_records(knowledge_base_dir=knowledge_base_dir))
    return records


def balance_capabilities(
    records: list[ShareGPTRecord],
    *,
    targets: dict[str, int] | None = None,
) -> list[ShareGPTRecord]:
    """去重后按能力采样到目标量，保证训练配比均衡（Text2SQL 为主、其余补齐）。"""
    targets = targets or CAPABILITY_TARGETS
    balanced: list[ShareGPTRecord] = []
    by_capability: dict[str, list[ShareGPTRecord]] = {}
    for record in records:
        capability = record.metadata.get("capability", "unknown")
        by_capability.setdefault(capability, []).append(record)
    for capability, group in by_capability.items():
        balanced.extend(sample_to(group, targets.get(capability, len(group))))
    return balanced


def load_spider_text2sql_records(data_dir: Path) -> list[ShareGPTRecord]:
    from training.dataset_builder.spider.convert import (  # 延迟导入避免重依赖
        build_text2sql_records,
        load_tables_by_db,
    )
    from training.dataset_builder.spider.download import SpiderSource

    source = SpiderSource(data_dir / "raw")
    splits = source.load()
    rows = splits.get("train", []) + splits.get("dev", [])
    if not rows:
        return []
    tables_by_db = load_tables_by_db(data_dir / "raw" / "spider" / "tables.json", rows)
    return build_text2sql_records(rows, tables_by_db)


def load_spider_eval_cases(data_dir: Path) -> list[dict]:
    from training.dataset_builder.spider.convert import (  # 延迟导入避免重依赖
        build_text2sql_cases,
        load_tables_by_db,
    )
    from training.dataset_builder.spider.download import SpiderSource

    source = SpiderSource(data_dir / "raw")
    splits = source.load()
    dev_rows = splits.get("dev", [])
    if not dev_rows:
        return []
    tables_by_db = load_tables_by_db(data_dir / "raw" / "spider" / "tables.json", dev_rows)
    return build_text2sql_cases(dev_rows, tables_by_db)


def _write_eval_cases(data_dir: Path) -> None:
    """写出评测样例：text2sql_cases（Spider dev）与 warehouse_cases（需求模板）。"""
    import json as _json

    from training.dataset_builder.common.render_prompt import (
        warehouse_system_prompt,
        warehouse_user_prompt,
    )
    from training.dataset_builder.warehouse.requirement_templates import generate_requirements

    processed_dir = data_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    try:
        text2sql_cases = load_spider_eval_cases(data_dir)
    except Exception as exc:  # noqa: BLE001
        text2sql_cases = []
        print(f"[warning] Text2SQL 评测用例生成失败：{exc}", file=sys.stderr)

    warehouse_cases = [
        {
            "requirement": requirement,
            "system": warehouse_system_prompt(),
            "prompt": warehouse_user_prompt(requirement=requirement),
        }
        for requirement in generate_requirements()
    ][:200]

    for filename, cases in (
        ("text2sql_cases.jsonl", text2sql_cases),
        ("warehouse_cases.jsonl", warehouse_cases),
    ):
        path = processed_dir / filename
        with path.open("w", encoding="utf-8") as handle:
            for case in cases:
                handle.write(_json.dumps(case, ensure_ascii=False))
                handle.write("\n")
        print(f"[info] 评测用例已写出：{path.relative_to(data_dir)}（{len(cases)} 条）")


def build(
    *,
    data_dir: Path,
    min_total: int = 3200,
    force: bool = False,
    include_text2sql: bool = True,
) -> dict:
    processed_dir = data_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    train_path = processed_dir / "train.jsonl"
    eval_path = processed_dir / "eval.jsonl"
    stats_path = processed_dir / "stats.json"

    if train_path.is_file() and eval_path.is_file() and not force:
        return json.loads(stats_path.read_text(encoding="utf-8"))

    records = build_capability_records(PROJECT_ROOT / "knowledge_base")
    if include_text2sql:
        try:
            records.extend(load_spider_text2sql_records(data_dir))
        except Exception as exc:  # noqa: BLE001 - 数据集缺失时应给出清晰提示
            print(f"[warning] Spider Text2SQL 数据加载失败：{exc}", file=sys.stderr)

    if not records:
        raise RuntimeError("没有生成任何数据集记录，请检查 knowledge_base/ 与各构建器。")

    deduped, dedup_stats = dedupe(records)
    balanced = balance_capabilities(deduped)
    train, eval_set = stratified_split(balanced)
    write_jsonl(train, train_path)
    write_jsonl(eval_set, eval_path)
    _write_eval_cases(data_dir)

    stats = {
        "total": len(balanced),
        "train": len(train),
        "eval": len(eval_set),
        "dedup": dedup_stats,
        "targets": CAPABILITY_TARGETS,
        "train_by_capability": counts_by_capability(train),
        "eval_by_capability": counts_by_capability(eval_set),
        "min_total": min_total,
    }
    if stats["total"] < min_total:
        raise RuntimeError(
            f"数据集总量 {stats['total']} 小于最低要求 {min_total}。"
            "请先下载 Spider 数据（python -m training.dataset_builder.spider.download），"
            "或调低 --min-total 用于快速验证。"
        )
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="构建数据工程 SFT 指令数据集")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "training/data")
    parser.add_argument("--min-total", type=int, default=3200)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-text2sql", action="store_true")
    args = parser.parse_args()

    stats = build(
        data_dir=args.data_dir,
        min_total=args.min_total,
        force=args.force,
        include_text2sql=not args.no_text2sql,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
