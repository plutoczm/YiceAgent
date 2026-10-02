"""数据集去重：精确去重 + 能力内问题近去重 + 跨能力问题去重。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from training.dataset_builder.common.normalize import normalize
from training.dataset_builder.common.records import ShareGPTRecord


def record_hash(record: ShareGPTRecord) -> str:
    """指令 + 答案的规范化 MD5，用于精确去重。"""
    payload = "\n".join(
        f"{turn.from_}:{normalize(turn.value)}" for turn in record.conversations
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def question_hash(record: ShareGPTRecord) -> str:
    return hashlib.md5(normalize(record.question()).encode("utf-8")).hexdigest()


def dedupe(
    records: Iterable[ShareGPTRecord],
) -> tuple[list[ShareGPTRecord], dict[str, int]]:
    """返回 (去重后记录, 统计)。精确重复丢弃；能力内问题重复丢弃；跨能力问题不重复。"""
    kept: list[ShareGPTRecord] = []
    exact_seen: set[str] = set()
    question_seen: set[str] = set()
    dropped_exact = 0
    dropped_question = 0

    for record in records:
        exact = record_hash(record)
        if exact in exact_seen:
            dropped_exact += 1
            continue
        question = question_hash(record)
        if question in question_seen:
            dropped_question += 1
            continue
        exact_seen.add(exact)
        question_seen.add(question)
        kept.append(record)

    return kept, {"kept": len(kept), "dropped_exact": dropped_exact, "dropped_question": dropped_question}
