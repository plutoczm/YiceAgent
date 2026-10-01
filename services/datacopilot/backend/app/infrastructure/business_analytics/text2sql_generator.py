from backend.app.application.business_analytics.errors import BusinessSQLGenerationError
from backend.app.application.business_analytics.managed_models import (
    ManagedGenericValidation,
    ManagedGenericValidationIssue,
    ManagedSQLDraft,
    ManagedSQLGenerationRequest,
    ManagedTokenUsage,
)
from backend.app.application.business_analytics.ports import ManagedSQLGeneratorPort
from backend.app.application.text2sql.models import Text2SQLResult
from backend.app.application.text2sql.text2sql_service import Text2SQLService


class Text2SQLServiceManagedGenerator(ManagedSQLGeneratorPort):
    """Thin adapter that pins Text2SQLService to pipeline-rendered schema input."""

    def __init__(self, *, text2sql_service: Text2SQLService) -> None:
        self._text2sql_service = text2sql_service

    async def generate(self, request: ManagedSQLGenerationRequest) -> ManagedSQLDraft:
        try:
            result = await self._text2sql_service.generate(
                question=request.question,
                engine=request.engine,
                schema_context=request.rendered_schema,
                database_name=None,
                use_rag=False,
            )
            if not isinstance(result, Text2SQLResult):
                raise BusinessSQLGenerationError()
            validation = ManagedGenericValidation(
                is_valid=result.validation.is_valid,
                issues=tuple(
                    ManagedGenericValidationIssue(
                        code=issue.code,
                        severity=issue.severity,
                        object_name=issue.object_name,
                    )
                    for issue in result.validation.issues
                ),
            )
            return ManagedSQLDraft(
                candidate_sql=result.sql,
                generic_validation=validation,
                token_usage=ManagedTokenUsage(
                    prompt_tokens=result.token_usage.prompt_tokens,
                    completion_tokens=result.token_usage.completion_tokens,
                    total_tokens=result.token_usage.total_tokens,
                ),
                model_explanation=result.explanation,
            )
        except BusinessSQLGenerationError:
            raise
        except Exception:
            raise BusinessSQLGenerationError() from None
