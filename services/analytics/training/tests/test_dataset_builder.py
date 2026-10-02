"""数据集构建器测试。仅依赖标准库 + 项目 backend，不依赖训练重依赖。"""

import json

import pytest

from training.dataset_builder.common.dedup import dedupe, record_hash
from training.dataset_builder.common.records import build_record, write_jsonl
from training.dataset_builder.common.split import counts_by_capability, stratified_split
from training.dataset_builder.knowledge_qa.doc_parser import extract_qa_blocks
from training.dataset_builder.knowledge_qa.generate import build_knowledge_records
from training.dataset_builder.sql_review.generate import build_sql_review_records
from training.dataset_builder.warehouse.generate import build_warehouse_records


def test_build_record_produces_sharegpt_format() -> None:
    record = build_record(
        system="sys",
        human="user question",
        gpt="assistant answer",
        metadata={"capability": "text2sql"},
    )

    payload = record.model_dump(by_alias=True, mode="json")
    assert [turn["from"] for turn in payload["conversations"]] == ["system", "human", "gpt"]
    assert payload["conversations"][1]["value"] == "user question"
    assert payload["metadata"]["capability"] == "text2sql"
    assert record.question() == "user question"


def test_dedupe_removes_exact_and_question_duplicates() -> None:
    base = build_record(system="s", human="统计最近7天活跃用户", gpt="SELECT ...")
    duplicate = build_record(system="s", human="统计最近7天活跃用户", gpt="SELECT ...")
    similar = build_record(system="s", human="统计最近7天活跃用户", gpt="另一条SQL答案")
    distinct = build_record(system="s", human="设计电商数仓", gpt="...")

    kept, stats = dedupe([base, duplicate, similar, distinct])

    assert len(kept) == 2
    assert stats["dropped_exact"] == 1
    assert stats["dropped_question"] == 1


def test_record_hash_is_stable() -> None:
    first = build_record(system="s", human="q", gpt="a")
    second = build_record(system="s", human="q", gpt="a")

    assert record_hash(first) == record_hash(second)


def test_stratified_split_preserves_capability_ratio() -> None:
    records = [
        build_record(system="s", human=f"q{i}", gpt="a", metadata={"capability": "text2sql"})
        for i in range(100)
    ] + [
        build_record(system="s", human=f"w{i}", gpt="a", metadata={"capability": "warehouse_design"})
        for i in range(100)
    ]

    train, eval_set = stratified_split(records, eval_ratio=0.1, seed=42)

    train_counts = counts_by_capability(train)
    eval_counts = counts_by_capability(eval_set)
    assert train_counts["text2sql"] == 90
    assert train_counts["warehouse_design"] == 90
    assert eval_counts["text2sql"] == 10
    assert eval_counts["warehouse_design"] == 10


def test_write_jsonl_roundtrip(tmp_path) -> None:
    records = [
        build_record(system="s", human=f"q{i}", gpt="a", metadata={"capability": "test"})
        for i in range(3)
    ]
    path = tmp_path / "out.jsonl"
    write_jsonl(records, path)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["conversations"][1]["value"] == "q0"


def test_warehouse_records_contain_gold_json_contract() -> None:
    records = build_warehouse_records()

    assert len(records) >= 2
    first = records[0]
    assert first.metadata["capability"] == "warehouse_design"
    answer = json.loads(first.conversations[-1].value)
    for key in ("ods", "dwd", "dws", "ads", "ddl", "metrics", "relationships"):
        assert key in answer
    assert len(answer["ods"]) > 0
    assert len(answer["ddl"]) > 0


def test_sql_review_records_are_rule_engine_annotated() -> None:
    records = build_sql_review_records(target=30)

    assert len(records) >= 20
    answer = json.loads(records[0].conversations[-1].value)
    assert "risk_level" in answer
    assert "score" in answer
    assert "issues" in answer


def test_knowledge_records_from_docs(tmp_path) -> None:
    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    (doc_dir / "spark.md").write_text(
        "# AQE\n\nSpark AQE 动态调整执行计划。\n",
        encoding="utf-8",
    )

    records = build_knowledge_records(knowledge_base_dir=doc_dir)

    assert len(records) >= 1
    assert records[0].metadata["capability"] == "knowledge_qa"
    assert "什么是AQE" in records[0].question()


def test_extract_qa_blocks_from_interview_doc(tmp_path) -> None:
    path = tmp_path / "qa.md"
    path.write_text(
        "Q: 什么是数据倾斜？\nA: 指 key 分布不均导致的任务倾斜。\nQ: 如何优化 JOIN？\nA: 使用 MapJoin。\n",
        encoding="utf-8",
    )

    pairs = extract_qa_blocks(path)

    assert len(pairs) == 2
    assert pairs[0] == ("什么是数据倾斜？", "指 key 分布不均导致的任务倾斜。")
