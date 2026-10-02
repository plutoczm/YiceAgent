from fastapi import APIRouter, Depends

from backend.app.application.text2sql.text2sql_service import Text2SQLService
from backend.app.presentation.api.dependencies.providers import get_text2sql_service
from backend.app.presentation.api.schemas.text2sql import (
    Text2SQLRequest,
    Text2SQLResponse,
)


router = APIRouter(prefix="/api/v1/text2sql", tags=["Text2SQL"])


@router.post(
    "",
    response_model=Text2SQLResponse,
    summary="根据自然语言生成 SQL",
    description="为 MySQL、Hive、Spark SQL 或 ClickHouse 生成经过校验的 SQL。",
)
async def generate_text2sql(
    request: Text2SQLRequest,
    service: Text2SQLService = Depends(get_text2sql_service),
) -> Text2SQLResponse:
    result = await service.generate(
        question=request.question,
        engine=request.engine,
        schema_context=request.schema_context,
        database_name=request.database_name,
        use_rag=request.use_rag,
        rag_collection_name=request.rag_collection_name,
    )
    return Text2SQLResponse(**result.model_dump())
