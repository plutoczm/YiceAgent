from typing import Any

import pytest

from backend.app.application.business_analytics.errors import BusinessSQLGenerationError
from backend.app.application.business_analytics.managed_models import (
    ManagedSQLGenerationRequest,
)
from backend.app.application.text2sql.models import (
    SQLEngine,
    SQLValidationIssue,
    SQLValidationResult,
    Text2SQLResult,
)
from backend.app.domain.ports.llm_provider import LLMUsage
from backend.app.infrastructure.business_analytics.text2sql_generator import (
    Text2SQLServiceManagedGenerator,
)


class FakeText2SQLService:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def text2sql_result() -> Text2SQLResult:
    return Text2SQLResult(
        sql="SELECT status FROM orders_v1 LIMIT 500",
        explanation="Candidate only",
        optimization_suggestions=[],
        engine=SQLEngine.HIVE,
        confidence=0.8,
        validation=SQLValidationResult(
            is_valid=True,
            issues=[
                SQLValidationIssue(
                    code="select_star",
                    severity="warning",
                    message="Explicit columns are preferred.",
                    object_name="orders_v1",
                )
            ],
        ),
        token_usage=LLMUsage(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        ),
    )


@pytest.mark.anyio
async def test_adapter_pins_managed_arguments_and_maps_untrusted_draft() -> None:
    service = FakeText2SQLService(result=text2sql_result())
    adapter = Text2SQLServiceManagedGenerator(text2sql_service=service)  # type: ignore[arg-type]
    request = ManagedSQLGenerationRequest(
        question="List order statuses",
        engine=SQLEngine.HIVE,
        rendered_schema="CREATE TABLE orders_v1 (status STRING);",
    )

    draft = await adapter.generate(request)

    assert service.calls == [
        {
            "question": "List order statuses",
            "engine": SQLEngine.HIVE,
            "schema_context": request.rendered_schema,
            "database_name": None,
            "use_rag": False,
        }
    ]
    assert draft.candidate_sql == "SELECT status FROM orders_v1 LIMIT 500"
    assert draft.generic_validation.is_valid is True
    assert draft.generic_validation.issues[0].code == "select_star"
    assert draft.generic_validation.issues[0].object_name == "orders_v1"
    assert draft.token_usage.total_tokens == 18
    assert draft.model_explanation == "Candidate only"


@pytest.mark.anyio
async def test_adapter_returns_safe_error_without_leaking_service_exception() -> None:
    adapter = Text2SQLServiceManagedGenerator(
        text2sql_service=FakeText2SQLService(
            error=RuntimeError("secret failure")
        )  # type: ignore[arg-type]
    )

    with pytest.raises(BusinessSQLGenerationError) as error:
        await adapter.generate(
            ManagedSQLGenerationRequest(
                question="List orders",
                engine=SQLEngine.HIVE,
                rendered_schema="CREATE TABLE orders_v1 (status STRING);",
            )
        )

    assert str(error.value) == "Managed SQL candidate could not be generated"
    assert "secret failure" not in str(error.value)
