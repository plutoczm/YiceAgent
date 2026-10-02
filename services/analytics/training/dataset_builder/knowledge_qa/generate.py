"""数据工程知识问答生成：基于 knowledge_base/ 文档。

- 普通文档：按章节生成"什么是{标题}？/ 请解释{标题}的原理与应用"问答。
- 面试题文档（data_engineer_top100.md）：直接提取已有的 Q/A 块。
"""

from __future__ import annotations

from pathlib import Path

from training.dataset_builder.common.records import ShareGPTRecord, build_record
from training.dataset_builder.common.render_prompt import knowledge_system_prompt
from training.dataset_builder.knowledge_qa.doc_parser import extract_qa_blocks, parse_markdown_sections


def _question_variants(heading: str) -> list[str]:
    return [
        f"什么是{heading}？",
        f"请解释{heading}的原理和应用场景。",
        f"说明{heading}在数据工程实践中的应用与注意事项。",
    ]


def build_knowledge_records(
    *,
    knowledge_base_dir: Path,
    max_body_chars: int = 600,
    max_per_doc: int = 8,
) -> list[ShareGPTRecord]:
    records: list[ShareGPTRecord] = []
    md_files = sorted(knowledge_base_dir.rglob("*.md"))
    for path in md_files:
        qa_pairs: list[tuple[str, str]] = []
        if "interview" in str(path).lower():
            qa_pairs = extract_qa_blocks(path)
        if qa_pairs:
            for question, answer in qa_pairs[:max_per_doc]:
                records.append(
                    build_record(
                        system=knowledge_system_prompt(),
                        human=question,
                        gpt=answer,
                        metadata={
                            "capability": "knowledge_qa",
                            "source": "knowledge_base",
                            "doc": path.stem,
                        },
                    )
                )
            continue

        for section in parse_markdown_sections(path):
            body = section.body[:max_body_chars].strip()
            if not body:
                continue
            for question in _question_variants(section.heading):
                records.append(
                    build_record(
                        system=knowledge_system_prompt(),
                        human=question,
                        gpt=body,
                        metadata={
                            "capability": "knowledge_qa",
                            "source": "knowledge_base",
                            "doc": path.stem,
                        },
                    )
                )
    return records
