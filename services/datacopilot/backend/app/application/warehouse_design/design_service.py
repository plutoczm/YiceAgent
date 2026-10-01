import json
import re
from typing import Any

from backend.app.application.rag.rag_service import RAGService
from backend.app.application.sql_review.sql_review_service import SQLReviewService
from backend.app.application.text2sql.models import SQLEngine
from backend.app.application.text2sql.text2sql_service import Text2SQLService
from backend.app.application.warehouse_design.exceptions import (
    WarehouseDesignGenerationError,
)
from backend.app.application.warehouse_design.models import (
    DDLStatement,
    DataFlowDesign,
    MetricDefinition,
    TableLayer,
    TableRelationship,
    WarehouseColumn,
    WarehouseDesignResult,
    WarehouseTable,
)
from backend.app.application.warehouse_design.prompt_builder import (
    WarehouseDesignPromptBuilder,
)
from backend.app.domain.ports.llm_provider import LLMProvider


COMMON_COLUMNS = [
    WarehouseColumn(name="dt", data_type="string", description="分区日期"),
    WarehouseColumn(name="etl_time", data_type="timestamp", description="ETL 处理时间"),
]


class WarehouseDesignService:
    def __init__(
        self,
        *,
        llm_provider: LLMProvider | None = None,
        rag_service: RAGService | None = None,
        text2sql_service: Text2SQLService | None = None,
        sql_review_service: SQLReviewService | None = None,
        prompt_builder: WarehouseDesignPromptBuilder | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.rag_service = rag_service
        self.text2sql_service = text2sql_service
        self.sql_review_service = sql_review_service
        self.prompt_builder = prompt_builder or WarehouseDesignPromptBuilder()

    async def design(
        self,
        *,
        requirement: str,
        use_rag: bool = False,
        rag_collection_name: str = "knowledge_base",
        recommendation_language: str = "zh-CN",
    ) -> WarehouseDesignResult:
        rag_context = None
        rag_error: str | None = None
        try:
            rag_context = await self._retrieve_rag_context(
                requirement,
                use_rag=use_rag,
                collection_name=rag_collection_name,
            )
        except Exception as exc:
            rag_error = self._error_message(exc)

        llm_payload: dict[str, Any] = {}
        llm_error: str | None = None
        llm_parse_error: str | None = None
        token_usage = None
        if self.llm_provider is not None:
            try:
                response = await self.llm_provider.chat(
                    self.prompt_builder.build_messages(
                        requirement=requirement,
                        rag_context=rag_context,
                        recommendation_language=recommendation_language,
                    ),
                    temperature=0.0,
                    max_tokens=3000,
                )
                token_usage = response.usage
                llm_payload = self._parse_llm_payload(response.content)
            except WarehouseDesignGenerationError as exc:
                llm_parse_error = str(exc)
            except Exception as exc:
                llm_error = self._error_message(exc)

        result = self._merge_with_template(
            requirement, llm_payload, recommendation_language=recommendation_language
        )
        if token_usage is not None:
            result.token_usage = token_usage
        result.metadata.update(
            {
                "rag_used": use_rag,
                "rag_context": rag_context or "",
                "llm_payload_used": bool(llm_payload),
                "recommendation_language": recommendation_language,
            }
        )
        if rag_error is not None:
            result.metadata["rag_error"] = rag_error
        if llm_error is not None:
            result.metadata["llm_error"] = llm_error
        if llm_parse_error is not None:
            result.metadata["llm_parse_error"] = llm_parse_error
        return result

    async def _retrieve_rag_context(
        self,
        requirement: str,
        *,
        use_rag: bool,
        collection_name: str,
    ) -> str | None:
        if not use_rag or self.rag_service is None:
            return None
        response = await self.rag_service.answer(
            requirement,
            collection_name=collection_name,
            top_k=5,
        )
        return response.answer

    @staticmethod
    def _error_message(exc: Exception) -> str:
        return str(exc) or exc.__class__.__name__

    def _parse_llm_payload(self, content: str) -> dict[str, Any]:
        stripped = content.strip()
        if not stripped:
            return {}
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
            stripped = re.sub(r"```$", "", stripped).strip()
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise WarehouseDesignGenerationError("LLM response must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise WarehouseDesignGenerationError("LLM response JSON must be an object")
        return payload

    def _merge_with_template(
        self,
        requirement: str,
        payload: dict[str, Any],
        *,
        recommendation_language: str = "zh-CN",
    ) -> WarehouseDesignResult:
        template = self.build_template_design(
            requirement, recommendation_language=recommendation_language
        )
        source_tables = self._parse_tables(payload.get("source_tables"), TableLayer.SOURCE)
        metrics = self._parse_metrics(payload.get("metrics"))
        recommendations = self._parse_string_list(payload.get("recommendations"))

        if source_tables:
            template.source_tables = self._deduplicate_tables(source_tables + template.source_tables)
        if metrics:
            template.metrics = self._deduplicate_metrics(metrics + template.metrics)
        if recommendations:
            template.recommendations = self._deduplicate_strings(
                recommendations + template.recommendations
            )
        template.ddl = self._build_ddl(template)
        return template

    @classmethod
    def build_template_design(
        cls, requirement: str, *, recommendation_language: str = "zh-CN"
    ) -> WarehouseDesignResult:
        domain = cls._detect_domain(requirement)
        tables = cls._domain_tables(domain)
        metrics = cls._domain_metrics(domain)
        relationships = cls._relationships(tables)
        data_flow = cls._data_flow(tables)
        recommendations = cls._recommendations(domain, recommendation_language)
        result = WarehouseDesignResult(
            requirement=requirement,
            source_tables=tables["source"],
            ods=tables["ods"],
            dwd=tables["dwd"],
            dws=tables["dws"],
            ads=tables["ads"],
            dim=tables["dim"],
            fact_tables=tables["fact"],
            relationships=relationships,
            ddl=[],
            metrics=metrics,
            data_flow=data_flow,
            recommendations=recommendations,
            metadata={"domain": domain},
        )
        result.ddl = cls._build_ddl(result)
        return result

    @classmethod
    def _detect_domain(cls, requirement: str) -> str:
        text = requirement.lower()
        if any(keyword in text for keyword in ("广告", "投放", "ad", "campaign")):
            return "advertising"
        if any(keyword in text for keyword in ("行为", "埋点", "user behavior", "event")):
            return "user_behavior"
        return "ecommerce"

    @classmethod
    def _domain_tables(cls, domain: str) -> dict[str, list[WarehouseTable]]:
        if domain == "advertising":
            entity = "ad"
            source_name = "ad_click_log"
            core_columns = [
                WarehouseColumn(name="ad_id", data_type="bigint", description="广告 ID"),
                WarehouseColumn(name="campaign_id", data_type="bigint", description="广告活动 ID"),
                WarehouseColumn(name="user_id", data_type="bigint", description="用户 ID"),
                WarehouseColumn(name="impression_cnt", data_type="bigint", description="曝光次数"),
                WarehouseColumn(name="click_cnt", data_type="bigint", description="点击次数"),
                WarehouseColumn(name="cost_amount", data_type="decimal(18,2)", description="广告成本"),
            ]
            dim = cls._table("dim_campaign", TableLayer.DIM, "Campaign dimension", core_columns[:2])
        elif domain == "user_behavior":
            entity = "user_behavior"
            source_name = "app_event_log"
            core_columns = [
                WarehouseColumn(name="event_id", data_type="string", description="事件 ID"),
                WarehouseColumn(name="user_id", data_type="bigint", description="用户 ID"),
                WarehouseColumn(name="event_name", data_type="string", description="事件名称"),
                WarehouseColumn(name="event_time", data_type="timestamp", description="事件时间"),
                WarehouseColumn(name="session_id", data_type="string", description="会话 ID"),
            ]
            dim = cls._table("dim_user", TableLayer.DIM, "User dimension", core_columns[1:2])
        else:
            entity = "order"
            source_name = "mysql_order"
            core_columns = [
                WarehouseColumn(name="order_id", data_type="bigint", description="订单 ID"),
                WarehouseColumn(name="user_id", data_type="bigint", description="用户 ID"),
                WarehouseColumn(name="sku_id", data_type="bigint", description="商品 SKU ID"),
                WarehouseColumn(name="pay_amount", data_type="decimal(18,2)", description="支付金额"),
                WarehouseColumn(name="order_time", data_type="timestamp", description="下单时间"),
                WarehouseColumn(name="province", data_type="string", description="省份"),
            ]
            dim = cls._table("dim_user", TableLayer.DIM, "User dimension", core_columns[1:2])

        source = cls._table(source_name, TableLayer.SOURCE, f"Raw {entity} source", core_columns)
        ods = cls._table(f"ods_{entity}_raw", TableLayer.ODS, f"ODS raw {entity} table", core_columns)
        dwd = cls._table(f"dwd_{entity}_detail", TableLayer.DWD, f"DWD cleaned {entity} detail", core_columns)
        dws = cls._table(f"dws_{entity}_day", TableLayer.DWS, f"DWS daily {entity} summary", core_columns)
        ads = cls._table(f"ads_{entity}_dashboard", TableLayer.ADS, f"ADS {entity} dashboard mart", core_columns)
        fact = cls._table(f"fact_{entity}", TableLayer.FACT, f"Fact table for {entity}", core_columns)
        return {
            "source": [source],
            "ods": [ods],
            "dwd": [dwd],
            "dws": [dws],
            "ads": [ads],
            "dim": [dim],
            "fact": [fact],
        }

    @classmethod
    def _table(
        cls,
        name: str,
        layer: TableLayer,
        description: str,
        columns: list[WarehouseColumn],
    ) -> WarehouseTable:
        return WarehouseTable(
            name=name,
            layer=layer,
            description=description,
            columns=cls._deduplicate_columns(columns + COMMON_COLUMNS),
            primary_keys=[columns[0].name] if columns else [],
            source_tables=[],
        )

    @classmethod
    def _domain_metrics(cls, domain: str) -> list[MetricDefinition]:
        common = [
            MetricDefinition(
                name="DAU",
                definition="Daily active users",
                calculation_logic="COUNT(DISTINCT user_id)",
                business_meaning="Measures daily user activity.",
            ),
            MetricDefinition(
                name="WAU",
                definition="Weekly active users",
                calculation_logic="COUNT(DISTINCT user_id) over 7 days",
                business_meaning="Measures weekly active scale.",
            ),
            MetricDefinition(
                name="MAU",
                definition="Monthly active users",
                calculation_logic="COUNT(DISTINCT user_id) over 30 days",
                business_meaning="Measures monthly active scale.",
            ),
            MetricDefinition(
                name="Retention Rate",
                definition="Users returning after a cohort start date",
                calculation_logic="retained_users / cohort_users",
                business_meaning="Measures product stickiness.",
            ),
            MetricDefinition(
                name="Conversion Rate",
                definition="Users completing target action divided by exposed users",
                calculation_logic="converted_users / exposed_users",
                business_meaning="Measures funnel efficiency.",
            ),
        ]
        if domain == "advertising":
            return [
                MetricDefinition(
                    name="Impressions",
                    definition="Total ad impressions",
                    calculation_logic="SUM(impression_cnt)",
                    business_meaning="Measures ad exposure.",
                ),
                MetricDefinition(
                    name="Clicks",
                    definition="Total ad clicks",
                    calculation_logic="SUM(click_cnt)",
                    business_meaning="Measures user engagement with ads.",
                ),
                MetricDefinition(
                    name="CTR",
                    definition="Click-through rate",
                    calculation_logic="SUM(click_cnt) / SUM(impression_cnt)",
                    business_meaning="Measures creative and targeting effectiveness.",
                ),
                MetricDefinition(
                    name="CPC",
                    definition="Cost per click",
                    calculation_logic="SUM(cost_amount) / SUM(click_cnt)",
                    business_meaning="Measures traffic acquisition cost.",
                ),
                MetricDefinition(
                    name="ROAS",
                    definition="Return on ad spend",
                    calculation_logic="SUM(revenue_amount) / SUM(cost_amount)",
                    business_meaning="Measures advertising ROI.",
                ),
                common[-1],
            ]
        ecommerce = [
            MetricDefinition(
                name="GMV",
                definition="Gross merchandise volume",
                calculation_logic="SUM(pay_amount)",
                business_meaning="Measures transaction scale.",
            ),
            MetricDefinition(
                name="ARPU",
                definition="Average revenue per user",
                calculation_logic="SUM(pay_amount) / COUNT(DISTINCT user_id)",
                business_meaning="Measures user monetization.",
            ),
        ]
        return common + ecommerce

    @classmethod
    def _relationships(cls, tables: dict[str, list[WarehouseTable]]) -> list[TableRelationship]:
        source = tables["source"][0].name
        ods = tables["ods"][0].name
        dwd = tables["dwd"][0].name
        dws = tables["dws"][0].name
        ads = tables["ads"][0].name
        return [
            TableRelationship(
                source_table=source,
                target_table=ods,
                relationship_type="ingestion",
                join_keys=[],
                description="将源数据以最少转换摄取到 ODS 层。",
            ),
            TableRelationship(
                source_table=ods,
                target_table=dwd,
                relationship_type="cleaning",
                join_keys=[],
                description="清洗、去重并标准化 ODS 记录，生成 DWD 明细。",
            ),
            TableRelationship(
                source_table=dwd,
                target_table=dws,
                relationship_type="aggregation",
                join_keys=["dt"],
                description="将 DWD 明细聚合为 DWS 日汇总。",
            ),
            TableRelationship(
                source_table=dws,
                target_table=ads,
                relationship_type="serving",
                join_keys=["dt"],
                description="将 DWS 汇总结果发布到 ADS 看板表。",
            ),
        ]

    @classmethod
    def _data_flow(cls, tables: dict[str, list[WarehouseTable]]) -> DataFlowDesign:
        ods = tables["ods"][0].name
        dwd = tables["dwd"][0].name
        dws = tables["dws"][0].name
        ads = tables["ads"][0].name
        return DataFlowDesign(
            pipeline_description=(
                "ODS receives raw source data, DWD standardizes detail records, "
                "DWS builds reusable aggregates, and ADS serves business dashboards."
            ),
            dependency_graph={
                ods: [tables["source"][0].name],
                dwd: [ods],
                dws: [dwd],
                ads: [dws],
            },
        )

    @classmethod
    def _recommendations(cls, domain: str, language: str = "zh-CN") -> list[str]:
        if language == "zh-CN":
            domain_names = {
                "ecommerce": "电商",
                "advertising": "广告投放",
                "user_behavior": "用户行为",
            }
            return [
                "分区策略：所有事实表和汇总表按 dt 字段分区。",
                "分桶策略：大型事实表按 user_id 或核心业务主键分桶。",
                "存储格式：Hive 表统一使用 Parquet 列式存储。",
                "压缩策略：使用 Snappy，在 CPU 开销与读写效率之间取得平衡。",
                "关联策略：广播小维度表，并对大型事实表进行预聚合。",
                f"业务域策略：围绕{domain_names.get(domain, domain)}场景优化指标与维度设计。",
            ]
        return [
            "Partition Strategy: partition all fact and summary tables by dt.",
            "Bucketing Strategy: bucket large fact tables by user_id or primary business key.",
            "Storage Format: use Parquet for Hive tables.",
            "Compression: use Snappy compression for balanced CPU and IO.",
            "Join Strategy: broadcast small dimension tables and pre-aggregate large facts.",
            f"Domain Strategy: tune metrics and dimensions for {domain}.",
        ]

    @classmethod
    def _build_ddl(cls, result: WarehouseDesignResult) -> list[DDLStatement]:
        tables = result.ods + result.dwd + result.dws + result.ads + result.dim + result.fact_tables
        return [
            DDLStatement(
                table_name=table.name,
                layer=table.layer,
                sql=cls._table_ddl(table),
            )
            for table in tables
        ]

    @classmethod
    def _table_ddl(cls, table: WarehouseTable) -> str:
        non_partition_columns = [
            column for column in table.columns if column.name not in set(table.partition_columns)
        ]
        column_lines = [
            f"    {column.name} {column.data_type} COMMENT '{column.description}'"
            for column in non_partition_columns
        ]
        return "\n".join(
            [
                f"CREATE TABLE {table.name} (",
                ",\n".join(column_lines),
                ")",
                "PARTITIONED BY (dt STRING)",
                "STORED AS PARQUET;",
            ]
        )

    def _parse_tables(self, values: Any, default_layer: TableLayer) -> list[WarehouseTable]:
        if not isinstance(values, list):
            return []
        tables: list[WarehouseTable] = []
        for value in values:
            if not isinstance(value, dict) or not value.get("name"):
                continue
            layer = TableLayer(value.get("layer", default_layer))
            columns = [
                WarehouseColumn(
                    name=str(column.get("name")),
                    data_type=str(column.get("data_type", "string")),
                    description=str(column.get("description", column.get("name"))),
                )
                for column in value.get("columns", [])
                if isinstance(column, dict) and column.get("name")
            ]
            tables.append(
                WarehouseTable(
                    name=str(value["name"]),
                    layer=layer,
                    description=str(value.get("description", value["name"])),
                    columns=self._deduplicate_columns(columns + COMMON_COLUMNS),
                    partition_columns=value.get("partition_columns", ["dt"]),
                    primary_keys=value.get("primary_keys", []),
                    source_tables=value.get("source_tables", []),
                )
            )
        return tables

    def _parse_metrics(self, values: Any) -> list[MetricDefinition]:
        if not isinstance(values, list):
            return []
        metrics: list[MetricDefinition] = []
        for value in values:
            if not isinstance(value, dict) or not value.get("name"):
                continue
            metrics.append(
                MetricDefinition(
                    name=str(value["name"]),
                    definition=str(value.get("definition", value["name"])),
                    calculation_logic=str(value.get("calculation_logic", "")),
                    business_meaning=str(value.get("business_meaning", value.get("definition", ""))),
                    source_tables=value.get("source_tables", []),
                )
            )
        return metrics

    def _parse_string_list(self, values: Any) -> list[str]:
        if isinstance(values, list):
            return [str(value) for value in values if str(value).strip()]
        return []

    @staticmethod
    def _deduplicate_columns(columns: list[WarehouseColumn]) -> list[WarehouseColumn]:
        seen: set[str] = set()
        result: list[WarehouseColumn] = []
        for column in columns:
            if column.name in seen:
                continue
            seen.add(column.name)
            result.append(column)
        return result

    @staticmethod
    def _deduplicate_tables(tables: list[WarehouseTable]) -> list[WarehouseTable]:
        seen: set[str] = set()
        result: list[WarehouseTable] = []
        for table in tables:
            if table.name in seen:
                continue
            seen.add(table.name)
            result.append(table)
        return result

    @staticmethod
    def _deduplicate_metrics(metrics: list[MetricDefinition]) -> list[MetricDefinition]:
        seen: set[str] = set()
        result: list[MetricDefinition] = []
        for metric in metrics:
            if metric.name in seen:
                continue
            seen.add(metric.name)
            result.append(metric)
        return result

    @staticmethod
    def _deduplicate_strings(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result
