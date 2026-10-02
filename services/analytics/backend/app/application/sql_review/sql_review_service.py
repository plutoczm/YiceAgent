from backend.app.application.sql_review.models import (
    GeneratedSQLReviewResult,
    SQLReviewResult,
)
from backend.app.application.sql_review.rules import SQLReviewRuleEngine
from backend.app.application.text2sql.models import SQLEngine
from backend.app.application.text2sql.text2sql_service import Text2SQLService
from backend.app.domain.ports.llm_provider import LLMMessage, LLMProvider


class SQLReviewService:
    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        rule_engine: SQLReviewRuleEngine | None = None,
        text2sql_service: Text2SQLService | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.rule_engine = rule_engine or SQLReviewRuleEngine()
        self.text2sql_service = text2sql_service

    async def review(
        self,
        *,
        sql: str,
        engine: SQLEngine,
        include_llm_explanation: bool = True,
    ) -> SQLReviewResult:
        rule_result = self.rule_engine.review(sql, engine=engine)
        if not include_llm_explanation:
            return rule_result

        llm_response = await self.llm_provider.chat(
            self._build_explanation_messages(sql, rule_result),
            temperature=0.0,
            max_tokens=600,
        )
        return rule_result.model_copy(
            update={
                "llm_explanation": llm_response.content.strip(),
                "token_usage": llm_response.usage,
                "metadata": {
                    **rule_result.metadata,
                    "llm_provider": llm_response.provider,
                    "llm_model": llm_response.model,
                },
            }
        )

    async def review_generated_sql(
        self,
        *,
        question: str,
        engine: SQLEngine,
        schema_context: str | None = None,
        database_name: str | None = None,
        use_rag: bool = False,
    ) -> GeneratedSQLReviewResult:
        if self.text2sql_service is None:
            raise ValueError("text2sql_service is required for review_generated_sql")
        generated_sql = await self.text2sql_service.generate(
            question=question,
            engine=engine,
            schema_context=schema_context,
            database_name=database_name,
            use_rag=use_rag,
        )
        review = await self.review(sql=generated_sql.sql, engine=engine)
        return GeneratedSQLReviewResult(generated_sql=generated_sql, review=review)

    def _build_explanation_messages(
        self,
        sql: str,
        rule_result: SQLReviewResult,
    ) -> list[LLMMessage]:
        issues = "\n".join(
            [
                f"- {issue.code} [{issue.severity.value}]: "
                f"{issue.description} Suggestion: {issue.suggestion}"
                for issue in rule_result.issues
            ]
        ) or "- No major rule issues."
        prompt = "\n\n".join(
            [
                "Rules are authoritative. Do not override rule findings.",
                "Explain why each issue exists, why the optimization helps, and the business impact.",
                f"Risk level: {rule_result.risk_level.value}",
                f"Score: {rule_result.score}",
                f"Issues:\n{issues}",
                f"SQL:\n{sql}",
                "Write a concise review explanation for a data engineering audience.",
            ]
        )
        return [
            LLMMessage(
                role="system",
                content="You explain SQL performance and quality reviews without inventing findings.",
            ),
            LLMMessage(role="user", content=prompt),
        ]
