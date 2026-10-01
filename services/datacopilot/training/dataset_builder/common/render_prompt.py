"""指令提示渲染：镜像应用运行时 PromptBuilder 的输出，保证微调模型适配线上提示。

这些函数与 backend/app/application/text2sql/prompt_builder.py 及
warehouse_design/prompt_builder.py 保持同构，避免出现"训练提示与线上提示"漂移。
"""

from __future__ import annotations

from backend.app.application.text2sql.models import DatabaseSchema, SQLEngine
from backend.app.application.text2sql.prompt_builder import ENGINE_RULES


def text2sql_system_prompt() -> str:
    return "\n".join(
        [
            "You are DataPilot-AI Text2SQL, a senior data warehouse SQL engineer.",
            "Generate production-ready analytical SQL for data engineering interviews.",
            "Return only valid JSON with keys: sql, explanation, optimization_suggestions, confidence.",
        ]
    )


def text2sql_user_prompt(
    *,
    question: str,
    engine: SQLEngine,
    schema: DatabaseSchema,
    rag_context: str | None = None,
) -> str:
    engine_rules = ENGINE_RULES[engine]
    rendered_schema = schema.render_for_prompt() or "No structured schema was provided."
    rag_section = rag_context or "None."
    return "\n\n".join(
        [
            "\n".join(
                [
                    "Rules:",
                    "1. Use only provided tables.",
                    "2. Never invent columns.",
                    "3. Never invent tables.",
                    "4. Prefer explicit aliases.",
                    "5. Generate readable SQL.",
                    "6. Explain business logic.",
                    "7. Follow target engine syntax.",
                ]
            ),
            f"Target engine: {engine.value}",
            "Engine-specific rules:\n" + "\n".join(f"- {rule}" for rule in engine_rules),
            f"Provided schema:\n{rendered_schema}",
            f"Retrieved schema documentation:\n{rag_section}",
            f"Natural language request:\n{question}",
            "\n".join(
                [
                    "JSON response contract:",
                    "{",
                    '  "sql": "SQL string",',
                    '  "explanation": "business logic explanation",',
                    '  "optimization_suggestions": ["hint 1", "hint 2"],',
                    '  "confidence": 0.0',
                    "}",
                ]
            ),
        ]
    )


def warehouse_system_prompt() -> str:
    return "\n".join(
        [
            "You are DataPilot-AI Warehouse Designer, a senior data warehouse architect.",
            "Generate complete, production-ready data warehouse designs for data engineering teams.",
            "Return only valid JSON. The application will validate and complete missing fields.",
        ]
    )


def warehouse_user_prompt(
    *,
    requirement: str,
    rag_context: str | None = None,
    recommendation_language: str = "zh-CN",
) -> str:
    rag_section = rag_context or "None."
    language_note = (
        "Recommendation Language: write every item in recommendations in Simplified Chinese."
        if recommendation_language == "zh-CN"
        else "Recommendation Language: write every item in recommendations in English."
    )
    return "\n\n".join(
        [
            "Business Requirement:",
            requirement,
            "\n".join(
                [
                    "Generate these warehouse layers and artifacts:",
                    "- Source Tables",
                    "- ODS",
                    "- DWD",
                    "- DWS",
                    "- ADS",
                    "- DIM",
                    "- Fact Tables",
                    "- Relationships",
                    "- Data Flow: ODS -> DWD -> DWS -> ADS",
                    "- Dependency Graph",
                ]
            ),
            "\n".join(
                [
                    "DDL Requirements:",
                    "- Generate Hive-compatible DDL.",
                    "- Include PARTITIONED BY (dt STRING).",
                    "- Include STORED AS PARQUET.",
                    "- Prefer clear column comments and stable table names.",
                ]
            ),
            language_note,
            f"Retrieved requirement/table context:\n{rag_section}",
            "\n".join(
                [
                    "JSON response contract:",
                    "{",
                    '  "source_tables": [],',
                    '  "ods": [],',
                    '  "dwd": [],',
                    '  "dws": [],',
                    '  "ads": [],',
                    '  "dim": [],',
                    '  "fact_tables": [],',
                    '  "relationships": [],',
                    '  "ddl": [],',
                    '  "metrics": [],',
                    '  "recommendations": []',
                    "}",
                ]
            ),
        ]
    )


def sql_review_system_prompt() -> str:
    return "You are DataPilot-AI SQL Reviewer, a data warehouse SQL performance expert."


def sql_review_user_prompt(*, sql: str, engine: SQLEngine) -> str:
    return "\n\n".join(
        [
            f"Review the following {engine.value} SQL and return a JSON report.",
            "Rules are authoritative. Do not invent findings.",
            "Return only valid JSON with keys: risk_level, score, issues, optimization_suggestions.",
            f"SQL:\n{sql}",
        ]
    )


def knowledge_system_prompt() -> str:
    return (
        "You are DataPilot-AI, a senior data engineering expert. "
        "Answer data engineering questions with accurate, technical detail."
    )
