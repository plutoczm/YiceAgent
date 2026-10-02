from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from backend.app.application.rag.models import RAGResponse, RetrievalMode
from backend.app.application.rag.rag_service import RAGService
from backend.app.application.sql_review.models import SQLReviewResult
from backend.app.application.sql_review.sql_review_service import SQLReviewService
from backend.app.application.text2sql.models import SQLEngine, Text2SQLResult
from backend.app.application.text2sql.text2sql_service import Text2SQLService
from backend.app.application.warehouse_design.design_service import WarehouseDesignService
from backend.app.application.warehouse_design.models import WarehouseDesignResult
from backend.app.application.business_analytics.models import BusinessAnalyticsRequest


class RAGToolInput(BaseModel):
    question: str = Field(min_length=1, description="需要从知识库回答的问题")
    collection_name: str = "knowledge_base"
    top_k: int = Field(default=5, ge=1, le=20)
    metadata_filter: dict[str, str | int | float | bool] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID


class Text2SQLToolInput(BaseModel):
    question: str = Field(min_length=1)
    engine: SQLEngine = SQLEngine.HIVE
    schema_context: str | None = None
    database_name: str | None = None
    use_rag: bool = False
    rag_collection_name: str = "knowledge_base"


class SQLReviewToolInput(BaseModel):
    sql: str = Field(min_length=1)
    engine: SQLEngine = SQLEngine.HIVE
    include_llm_explanation: bool = True


class WarehouseDesignToolInput(BaseModel):
    requirement: str = Field(min_length=1)
    use_rag: bool = False
    rag_collection_name: str = "knowledge_base"


class BusinessAnalyticsAgentToolInput(BusinessAnalyticsRequest):
    """Model-visible analytics fields only; trusted context is injected by the server."""


class AgentToolbox:
    """在智能体边界使用 JSON Schema 校验的 LangChain 工具箱。"""

    def __init__(
        self,
        *,
        rag_service: RAGService,
        text2sql_service: Text2SQLService,
        sql_review_service: SQLReviewService,
        warehouse_design_service: WarehouseDesignService,
    ) -> None:
        async def query_knowledge(**kwargs: Any) -> RAGResponse:
            return await rag_service.answer(**kwargs)

        async def generate_sql(**kwargs: Any) -> Text2SQLResult:
            return await text2sql_service.generate(**kwargs)

        async def review_sql(**kwargs: Any) -> SQLReviewResult:
            return await sql_review_service.review(**kwargs)

        async def design_warehouse(**kwargs: Any) -> WarehouseDesignResult:
            return await warehouse_design_service.design(**kwargs)

        self._tools = {
            "rag": StructuredTool.from_function(
                coroutine=query_knowledge,
                name="query_knowledge_base",
                description="检索带引用依据的数据工程知识。",
                args_schema=RAGToolInput,
            ),
            "text2sql": StructuredTool.from_function(
                coroutine=generate_sql,
                name="generate_sql",
                description="根据自然语言需求生成经过校验的 SQL。",
                args_schema=Text2SQLToolInput,
            ),
            "sql_review": StructuredTool.from_function(
                coroutine=review_sql,
                name="review_sql",
                description="审核 SQL 的正确性、风险和性能问题。",
                args_schema=SQLReviewToolInput,
            ),
            "warehouse_design": StructuredTool.from_function(
                coroutine=design_warehouse,
                name="design_data_warehouse",
                description="设计 ODS、DWD、DWS、ADS、维度、指标和 DDL。",
                args_schema=WarehouseDesignToolInput,
            ),
        }

    async def invoke(self, tool_key: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools[tool_key]
        return await tool.ainvoke(arguments)

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "key": key,
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.args_schema.model_json_schema(),
            }
            for key, tool in self._tools.items()
        ]
