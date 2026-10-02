from fastapi import APIRouter, Depends

from backend.app.application.warehouse_design.design_service import WarehouseDesignService
from backend.app.presentation.api.dependencies.providers import get_warehouse_design_service
from backend.app.presentation.api.schemas.warehouse_design import (
    WarehouseDesignRequest,
    WarehouseDesignResponse,
)


router = APIRouter(prefix="/api/v1/warehouse-design", tags=["Warehouse Design"])


@router.post(
    "",
    response_model=WarehouseDesignResponse,
    summary="生成数仓设计",
    description="生成分层数仓设计、Hive DDL、指标、数据流和建议。",
)
async def design_warehouse(
    request: WarehouseDesignRequest,
    service: WarehouseDesignService = Depends(get_warehouse_design_service),
) -> WarehouseDesignResponse:
    result = await service.design(
        requirement=request.requirement,
        use_rag=request.use_rag,
        rag_collection_name=request.rag_collection_name,
        recommendation_language=request.recommendation_language,
    )
    return WarehouseDesignResponse(**result.model_dump())
