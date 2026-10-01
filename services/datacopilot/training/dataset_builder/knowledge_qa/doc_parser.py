"""解析 knowledge_base 文档为 (标题, 正文) 单元，供生成问答对。"""

from __future__ import annotations

import re
from pathlib import Path


class Section:
    def __init__(self, heading: str, body: str) -> None:
        self.heading = heading
        self.body = body


def parse_markdown_sections(path: Path) -> list[Section]:
    """按 Markdown 标题（# 到 ####）切分文档。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    sections: list[Section] = []
    current_heading = ""
    current_body: list[str] = []
    heading_pattern = re.compile(r"^#{1,4}\s+(.*)$")

    def flush() -> None:
        body = "\n".join(current_body).strip()
        if current_heading and body:
            sections.append(Section(current_heading.strip(), body))

    for line in lines:
        match = heading_pattern.match(line)
        if match:
            flush()
            current_heading = match.group(1)
            current_body = []
        else:
            current_body.append(line)
    flush()
    return sections


def extract_qa_blocks(path: Path) -> list[tuple[str, str]]:
    """从形如 'Q: ...' / 'A: ...' 的文档中提取问答对（用于面试题文档）。"""
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(
        r"(?:^|\n)\s*Q[:：]\s*(.+?)\n\s*A[:：]\s*(.+?)(?=\n\s*Q[:：]|\Z)",
        text,
        flags=re.DOTALL,
    )
    return [(q.strip(), a.strip()) for q, a in blocks]
