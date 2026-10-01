"""Agent 结果校验。

validate_result 节点在领域节点执行后、format_response 前运行，对各类意图的
输出做防御式校验（绝不抛异常），将结论写入 AgentState.validation 透出给调用方。
校验结果为可观测信息，不阻断 agent 主流程。
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from backend.app.application.agent.models import AgentIntent
from backend.app.application.agent.state import AgentState
from backend.app.application.sql_review.rules import SQLReviewRuleEngine
from backend.app.application.text2sql.schema_service import SchemaService
from backend.app.application.text2sql.sql_validator import SQLValidator


RAG_NO_CONTEXT_ANSWER = "I do not have enough retrieved context to answer this question reliably."


class ValidationStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


class ValidationCheck(BaseModel):
    code: str
    status: ValidationStatus
    message: str


class ResultValidation(BaseModel):
    is_valid: bool
    checks: list[ValidationCheck] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class ResultValidator:
    def __init__(
        self,
        *,
        sql_validator: SQLValidator | None = None,
        schema_service: SchemaService | None = None,
        rule_engine: SQLReviewRuleEngine | None = None,
    ) -> None:
        self.sql_validator = sql_validator or SQLValidator()
        self.schema_service = schema_service or SchemaService()
        self.rule_engine = rule_engine or SQLReviewRuleEngine()

    def validate(self, state: AgentState) -> ResultValidation:
        intent = state["intent"]
        if intent is AgentIntent.RAG:
            checks = self._validate_rag(state)
        elif intent in (AgentIntent.TEXT2SQL, AgentIntent.TEXT2SQL_SQL_REVIEW):
            checks = self._validate_text2sql(state)
        elif intent is AgentIntent.SQL_REVIEW:
            checks = self._validate_sql_review(state)
        elif intent is AgentIntent.WAREHOUSE_DESIGN:
            checks = self._validate_warehouse_design(state)
        else:
            checks = [
                ValidationCheck(
                    code="no_structured_output",
                    status=ValidationStatus.OK,
                    message="No structured output to validate for this intent.",
                )
            ]
        is_valid = not any(check.status is ValidationStatus.ERROR for check in checks)
        return ResultValidation(
            is_valid=is_valid,
            checks=checks,
            summary=self._summarize(checks),
        )

    def _validate_rag(self, state: AgentState) -> list[ValidationCheck]:
        context = state.get("retrieved_context")
        if context is None:
            return [
                ValidationCheck(
                    code="rag_missing",
                    status=ValidationStatus.ERROR,
                    message="No RAG context was produced.",
                )
            ]
        checks: list[ValidationCheck] = []
        if not context.answer.strip() or context.answer.strip() == RAG_NO_CONTEXT_ANSWER:
            checks.append(
                ValidationCheck(
                    code="rag_no_answer",
                    status=ValidationStatus.ERROR,
                    message="RAG returned an empty or insufficient-context answer.",
                )
            )
        else:
            checks.append(
                ValidationCheck(
                    code="rag_answer_present",
                    status=ValidationStatus.OK,
                    message="RAG answer is present.",
                )
            )
        if not context.citations:
            checks.append(
                ValidationCheck(
                    code="rag_no_citations",
                    status=ValidationStatus.WARN,
                    message="RAG response has no citations.",
                )
            )
        if not context.retrieved_chunks:
            checks.append(
                ValidationCheck(
                    code="rag_no_retrieved_chunks",
                    status=ValidationStatus.WARN,
                    message="RAG response has no retrieved chunks.",
                )
            )
        return checks

    def _validate_text2sql(self, state: AgentState) -> list[ValidationCheck]:
        generated = state.get("generated_sql")
        if generated is None:
            return [
                ValidationCheck(
                    code="text2sql_missing",
                    status=ValidationStatus.ERROR,
                    message="No SQL was generated.",
                )
            ]
        sql = generated.sql.strip()
        if not sql:
            return [
                ValidationCheck(
                    code="text2sql_empty",
                    status=ValidationStatus.ERROR,
                    message="Generated SQL is empty.",
                )
            ]
        try:
            schema = self.schema_service.parse_schema_context(
                state["request"].schema_context,
                database_name=state["request"].database_name,
            )
            validation = self.sql_validator.validate(
                sql,
                schema=schema,
                engine=state["request"].engine,
            )
            error_count = sum(1 for issue in validation.issues if issue.severity == "error")
            warning_count = sum(
                1 for issue in validation.issues if issue.severity == "warning"
            )
            if error_count:
                status = ValidationStatus.ERROR
            elif warning_count:
                status = ValidationStatus.WARN
            else:
                status = ValidationStatus.OK
            return [
                ValidationCheck(
                    code="text2sql_valid",
                    status=status,
                    message=(
                        f"SQL validation: {error_count} errors, {warning_count} warnings."
                    ),
                )
            ]
        except Exception:
            return [
                ValidationCheck(
                    code="text2sql_validate_error",
                    status=ValidationStatus.WARN,
                    message="SQL could not be validated against the provided schema.",
                )
            ]

    def _validate_sql_review(self, state: AgentState) -> list[ValidationCheck]:
        review = state.get("review_result")
        if review is None:
            return [
                ValidationCheck(
                    code="sql_review_missing",
                    status=ValidationStatus.ERROR,
                    message="No SQL review result was produced.",
                )
            ]
        checks: list[ValidationCheck] = []
        if not (0 <= review.score <= 100):
            checks.append(
                ValidationCheck(
                    code="sql_review_score_out_of_range",
                    status=ValidationStatus.WARN,
                    message=f"Review score {review.score} is outside [0, 100].",
                )
            )
        if review.risk_level is None:
            checks.append(
                ValidationCheck(
                    code="sql_review_missing_risk",
                    status=ValidationStatus.WARN,
                    message="Review result has no risk level.",
                )
            )
        checks.append(self._check_review_consistency(state, review))
        return checks

    def _check_review_consistency(self, state: AgentState, review: Any) -> ValidationCheck:
        try:
            sql = self._extract_sql(state)
            re_run = self.rule_engine.review(sql, engine=state["request"].engine)
            expected = {issue.code for issue in re_run.issues}
            reported = {issue.code for issue in review.issues}
            extra = reported - expected
            if extra:
                return ValidationCheck(
                    code="sql_review_inconsistent",
                    status=ValidationStatus.WARN,
                    message=f"Reported issues not reproduced by rule engine: {sorted(extra)}.",
                )
            return ValidationCheck(
                code="sql_review_consistent",
                status=ValidationStatus.OK,
                message="Review findings are consistent with the rule engine.",
            )
        except Exception:
            return ValidationCheck(
                code="sql_review_rerun_error",
                status=ValidationStatus.WARN,
                message="Review findings could not be re-verified.",
            )

    def _validate_warehouse_design(self, state: AgentState) -> list[ValidationCheck]:
        design = state.get("warehouse_design")
        if design is None:
            return [
                ValidationCheck(
                    code="warehouse_design_missing",
                    status=ValidationStatus.ERROR,
                    message="No warehouse design was produced.",
                )
            ]
        checks: list[ValidationCheck] = []
        covered_layers: list[str] = []
        for layer in ("ods", "dwd", "dws", "ads"):
            tables = getattr(design, layer, None)
            if tables:
                covered_layers.append(layer)
            else:
                checks.append(
                    ValidationCheck(
                        code=f"warehouse_layer_{layer}_empty",
                        status=ValidationStatus.ERROR,
                        message=f"Warehouse {layer.upper()} layer is empty.",
                    )
                )
        if not design.relationships:
            checks.append(
                ValidationCheck(
                    code="warehouse_no_relationships",
                    status=ValidationStatus.WARN,
                    message="Warehouse design has no table relationships.",
                )
            )
        if not design.metrics:
            checks.append(
                ValidationCheck(
                    code="warehouse_no_metrics",
                    status=ValidationStatus.WARN,
                    message="Warehouse design has no metrics.",
                )
            )
        if not design.ddl:
            checks.append(
                ValidationCheck(
                    code="warehouse_no_ddl",
                    status=ValidationStatus.WARN,
                    message="Warehouse design has no DDL statements.",
                )
            )
        return checks

    def _extract_sql(self, state: AgentState) -> str:
        generated = state.get("generated_sql")
        if generated is not None:
            return generated.sql
        query = state["query"]
        for marker in (":", "："):
            if marker in query:
                return query.split(marker, 1)[1].strip()
        return query

    def _summarize(self, checks: list[ValidationCheck]) -> dict[str, Any]:
        return {
            "total": len(checks),
            "errors": sum(1 for check in checks if check.status is ValidationStatus.ERROR),
            "warnings": sum(1 for check in checks if check.status is ValidationStatus.WARN),
            "ok": sum(1 for check in checks if check.status is ValidationStatus.OK),
        }
