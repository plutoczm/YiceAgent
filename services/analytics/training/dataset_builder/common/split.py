"""分层 train/eval 划分：按能力分别做 90/10 分层，seed 固定保证可复现。"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Iterable, Sequence

from training.dataset_builder.common.records import ShareGPTRecord


def stratified_split(
    records: Iterable[ShareGPTRecord],
    *,
    eval_ratio: float = 0.1,
    seed: int = 42,
) -> tuple[list[ShareGPTRecord], list[ShareGPTRecord]]:
    """按 metadata.capability 分层随机划分。"""
    by_capability: dict[str, list[ShareGPTRecord]] = defaultdict(list)
    for record in records:
        by_capability[record.metadata.get("capability", "unknown")].append(record)

    train: list[ShareGPTRecord] = []
    eval_set: list[ShareGPTRecord] = []
    rng = random.Random(seed)
    for capability, group in sorted(by_capability.items()):
        rng.shuffle(group)
        split_index = int(len(group) * (1 - eval_ratio))
        train.extend(group[:split_index])
        eval_set.extend(group[split_index:])
    return train, eval_set


def counts_by_capability(records: Sequence[ShareGPTRecord]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[record.metadata.get("capability", "unknown")] += 1
    return dict(counts)


def sample_to(
    records: list[ShareGPTRecord],
    target: int,
    *,
    seed: int = 42,
) -> list[ShareGPTRecord]:
    """按固定种子采样到目标数量；不足目标时原样返回，保持能力配比。"""
    if len(records) <= target:
        return records
    return random.Random(seed).sample(records, target)
