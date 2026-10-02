from fastapi import APIRouter, Depends

from backend.app.application.sql_review.sql_review_service import SQLReviewService
from backend.app.presentation.api.dependencies.providers import get_sql_review_service
from backend.app.presentation.api.schemas.sql_review import (
    GeneratedSQLReviewRequest,
    GeneratedSQLReviewResponse,
    SQLReviewRequest,
    SQLReviewResponse,
)


router = APIRouter(prefix="/api/v1/sql-review", tags=["SQL Review"])


@router.post(
    "",
    response_model=SQLReviewResponse,
    summary="审核 SQL 质量与性能",
    description="分析 SQL 风险、质量评分并返回基于规则的优化建议。",
)
async def review_sql(
    request: SQLReviewRequest,
    service: SQLReviewService = Depends(get_sql_review_service),
) -> SQLReviewResponse:
    result = await service.review(
        sql=request.sql,
        engine=request.engine,
        include_llm_explanation=request.include_llm_explanation,
    )
    return SQLReviewResponse(**result.model_dump())


@router.post(
    "/generated",
    response_model=GeneratedSQLReviewResponse,
    summary="生成并立即审核 SQL",
    description="在同一工作流中依次执行 Text2SQL 和 SQL 审核。",
)
async def review_generated_sql(
    request: GeneratedSQLReviewRequest,
    service: SQLReviewService = Depends(get_sql_review_service),
) -> GeneratedSQLReviewResponse:
    result = await service.review_generated_sql(
        question=request.question,
        engine=request.engine,
        schema_context=request.schema_context,
        database_name=request.database_name,
        use_rag=request.use_rag,
    )
    return GeneratedSQLReviewResponse(**result.model_dump())
