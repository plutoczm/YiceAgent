"""数仓设计指令生成：需求网格 x 模板设计（免 LLM 的确定性 gold 输出）。

gold 由应用层 WarehouseDesignService.build_template_design 生成，保证结构与
线上契约（source_tables/ods/dwd/dws/ads/dim/fact_tables/relationships/ddl/
metrics/recommendations）完全一致。
"""

from __future__ import annotations

import json

from backend.app.application.warehouse_design.design_service import WarehouseDesignService

from training.dataset_builder.common.records import ShareGPTRecord, build_record
from training.dataset_builder.common.render_prompt import (
    warehouse_system_prompt,
    warehouse_user_prompt,
)
from training.dataset_builder.warehouse.requirement_templates import (
    DEFAULT_RECOMMENDATION_LANGUAGES,
    generate_requirements,
)


OUTPUT_KEYS = (
    "source_tables",
    "ods",
    "dwd",
    "dws",
    "ads",
    "dim",
    "fact_tables",
    "relationships",
    "ddl",
    "metrics",
    "recommendations",
)


def _design_to_contract(design) -> dict:
    """把 WarehouseDesignResult 裁剪为线上 JSON 契约键。"""
    payload = design.model_dump(mode="json")
    return {key: payload.get(key) for key in OUTPUT_KEYS if key in payload}


def build_warehouse_records(
    *,
    requirements: list[str] | None = None,
    languages: tuple[str, ...] = DEFAULT_RECOMMENDATION_LANGUAGES,
) -> list[ShareGPTRecord]:
    records: list[ShareGPTRecord] = []
    for requirement in requirements or generate_requirements():
        for language in languages:
            design = WarehouseDesignService.build_template_design(
                requirement, recommendation_language=language
            )
            human = warehouse_user_prompt(
                requirement=requirement,
                recommendation_language=language,
            )
            answer = json.dumps(_design_to_contract(design), ensure_ascii=False)
            records.append(
                build_record(
                    system=warehouse_system_prompt(),
                    human=human,
                    gpt=answer,
                    metadata={
                        "capability": "warehouse_design",
                        "source": "template",
                        "domain": design.metadata.get("domain", "ecommerce"),
                        "language": language,
                    },
                )
            )
    return records
