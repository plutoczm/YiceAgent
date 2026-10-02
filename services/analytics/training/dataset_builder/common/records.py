"""ShareGPT 记录模型与 JSONL 读写。

数据集统一输出 ShareGPT JSONL 格式，兼容 Unsloth/TRL SFTTrainer：
{"conversations": [{"from": "system", "value": "..."},
                    {"from": "human", "value": "..."},
                    {"from": "gpt", "value": "..."}],
 "metadata": {"capability": "...", "engine": "...", "source": "...", "db_id": "..."}}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, Field


class ConversationTurn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    value: str


class ShareGPTRecord(BaseModel):
    conversations: list[ConversationTurn]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def question(self) -> str:
        """取 human 指令文本用于去重。"""
        for turn in self.conversations:
            if turn.from_ == "human":
                return turn.value
        return ""


def write_jsonl(records: list[ShareGPTRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.model_dump(by_alias=True), ensure_ascii=False))
            handle.write("\n")


def read_jsonl(path: Path) -> Iterator[ShareGPTRecord]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield ShareGPTRecord.model_validate(json.loads(line))


def build_record(
    *,
    system: str,
    human: str,
    gpt: str,
    metadata: dict[str, Any] | None = None,
) -> ShareGPTRecord:
    return ShareGPTRecord(
        conversations=[
            ConversationTurn(from_="system", value=system),
            ConversationTurn(from_="human", value=human),
            ConversationTurn(from_="gpt", value=gpt),
        ],
        metadata=metadata or {},
    )
