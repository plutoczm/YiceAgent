from backend.app.domain.ports.llm_provider import LLMMessage


class WarehouseDesignPromptBuilder:
    def build_messages(
        self,
        *,
        requirement: str,
        rag_context: str | None = None,
        recommendation_language: str = "zh-CN",
    ) -> list[LLMMessage]:
        system_prompt = "\n".join(
            [
                "You are DataPilot-AI Warehouse Designer, a senior data warehouse architect.",
                "Generate complete, production-ready data warehouse designs for data engineering teams.",
                "Return only valid JSON. The application will validate and complete missing fields.",
            ]
        )
        user_prompt = "\n\n".join(
            [
                "Business Requirement:",
                requirement,
                self._design_scope(),
                self._ddl_rules(),
                self._metric_rules(),
                self._recommendation_language(recommendation_language),
                self._rag_section(rag_context),
                self._output_contract(),
            ]
        )
        return [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]

    @staticmethod
    def _recommendation_language(language: str) -> str:
        if language == "zh-CN":
            return "Recommendation Language: write every item in recommendations in Simplified Chinese."
        return "Recommendation Language: write every item in recommendations in English."

    def _design_scope(self) -> str:
        return "\n".join(
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
        )

    def _ddl_rules(self) -> str:
        return "\n".join(
            [
                "DDL Requirements:",
                "- Generate Hive-compatible DDL.",
                "- Include PARTITIONED BY (dt STRING).",
                "- Include STORED AS PARQUET.",
                "- Prefer clear column comments and stable table names.",
            ]
        )

    def _metric_rules(self) -> str:
        return "\n".join(
            [
                "Metric Definitions:",
                "- Include Core Metrics.",
                "- Include Metric Definitions, Calculation Logic, and Business Meaning.",
                "- Consider DAU, WAU, MAU, GMV, ARPU, Retention Rate, and Conversion Rate when relevant.",
            ]
        )

    def _rag_section(self, rag_context: str | None) -> str:
        if not rag_context:
            return "Retrieved requirement/table context:\nNone."
        return f"Retrieved requirement/table context:\n{rag_context}"

    def _output_contract(self) -> str:
        return "\n".join(
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
        )
