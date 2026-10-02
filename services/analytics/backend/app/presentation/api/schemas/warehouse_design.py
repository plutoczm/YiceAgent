from typing import Literal

from pydantic import BaseModel, Field

from backend.app.application.warehouse_design.models import WarehouseDesignResult


class WarehouseDesignRequest(BaseModel):
    requirement: str = Field(min_length=1, examples=["设计电商订单分析数仓"])
    use_rag: bool = False
    rag_collection_name: str = "knowledge_base"
    recommendation_language: Literal["zh-CN", "en"] = "zh-CN"


class WarehouseDesignResponse(WarehouseDesignResult):
    pass
