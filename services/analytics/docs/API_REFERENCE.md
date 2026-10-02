# API 接口参考

默认地址：`http://127.0.0.1:8000`。交互式 Swagger 文档位于 `/docs`，OpenAPI JSON 位于 `/openapi.json`。

所有 JSON 请求使用 `Content-Type: application/json`。参数校验失败返回统一错误结构：

```json
{
  "error": {
    "code": "validation_error",
    "message": "请求参数校验失败",
    "details": []
  },
  "request_id": "7de8d6e0-..."
}
```

## 健康与配置

### `GET /health/live`

快速 liveness 检查，不调用 LLM 或向量库。启动器和 Docker healthcheck 使用该端点。

### `GET /health`

检查应用、大模型 Provider 和向量库。若知识问答返回 `502`，优先查看 `llm_provider.api_key_configured`、`reachable` 与返回的 `request_id`。

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "service": "DataPilot-AI",
  "status": "ok",
  "environment": "development",
  "llm_provider": {
    "provider": "deepseek",
    "ok": true,
    "model": "deepseek-chat"
  },
  "vector_store": {
    "status": "ok",
    "collection_name": "knowledge_base",
    "document_count": 0
  }
}
```

### `GET /api/v1/config/runtime`

返回不含密钥的运行环境、默认 Provider、Embedding 模型和能力开关。

## 知识库

### `POST /api/v1/knowledge/documents`

上传 PDF、DOCX、TXT 或 Markdown 文档。请求类型为 `multipart/form-data`。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/knowledge/documents \
  -F "file=@knowledge_base/spark/spark_aqe.md" \
  -F "collection_name=knowledge_base" \
  -F "domain=spark" \
  -F "tags=spark,aqe"
```

响应包含 `document_id`、文件信息、集合名和分块数量。

### `GET /api/v1/knowledge/documents`

查看当前进程登记的已摄取文档。

### `DELETE /api/v1/knowledge/documents/{document_id}`

删除文档登记信息和对应向量分块。

### `POST /api/v1/knowledge/query`

直接执行 RAG 查询。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/knowledge/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Spark AQE 有什么作用？",
    "collection_name": "knowledge_base",
    "top_k": 5,
    "retrieval_mode": "hybrid",
    "score_threshold": 0.2
  }'
```

关键参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `question` | string | 必填 | 用户问题 |
| `collection_name` | string | `knowledge_base` | 知识库集合 |
| `top_k` | integer | `5` | 最终召回数量，范围 1-20 |
| `retrieval_mode` | string | `hybrid` | `hybrid` 或 `vector` |
| `metadata_filter` | object | `null` | ChromaDB 元数据过滤条件 |
| `score_threshold` | number | `null` | 最低相关度，范围 0-1 |

响应包含 `answer`、`citations`、检索元数据和 Token 使用量。

## RAG 流式对话

### `POST /api/v1/chat/stream`

参数与知识库查询基本一致，问题字段名为 `message`。响应类型为 `text/event-stream`：

```text
event: token
data: {"text":"Spark AQE ..."}

event: citations
data: {"citations":[...]}

event: done
data: {"metadata":{...}}
```

## 统一智能体

### `GET /api/v1/agent/tools`

查看所有 LangChain 结构化工具的名称、中文说明和 Pydantic JSON Schema。

### `POST /api/v1/agent/chat`

自动识别意图并执行 RAG、Text2SQL、SQL 审核、数仓设计或通用对话。

传入 `user_id` 后，Agent 会召回该用户的显式长期记忆和规则；`session_id` 用于共享短期消息、摘要和最近一次 Agent state。生产环境必须从认证身份生成 `user_id`。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "统计最近7天活跃用户并检查 SQL",
    "user_id": "demo-user",
    "session_id": "demo-session",
    "engine": "hive",
    "schema_context": "dwd_user_behavior_detail(user_id bigint, dt string)",
    "retrieval_mode": "hybrid"
  }'
```

响应示例：

```json
{
  "intent": "TEXT2SQL_SQL_REVIEW",
  "result": {
    "generated_sql": {},
    "review": {}
  },
  "routing_path": [
    "classify_intent",
    "planner",
    "text2sql",
    "sql_review",
    "format_response"
  ],
  "final_response": "...",
  "metadata": {
    "intent_confidence": 0.95,
    "tool_usage": {"text2sql": 1, "sql_review": 1},
    "max_steps": 8,
    "memory": {
      "backend": "redis_chroma",
      "short_term_messages": 2,
      "checkpoint_available": true,
      "rules_loaded": 1,
      "long_term_memories_recalled": 1
    },
    "validation": {"total": 2, "errors": 0, "warnings": 0, "ok": 2}
  },
  "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

响应说明：

- `result.validation`：`validate_result` 节点的完整校验结果（Text2SQL 分支保留 SQL 级校验 `is_valid`/`issues`；RAG、SQL 审核、数仓设计分支为智能体级校验 `{is_valid, checks, summary}`）。
- `metadata.validation`：校验摘要 `{total, errors, warnings, ok}`。
- `metadata.memory`：当前短期记忆、checkpoint 与长期/规则召回统计。

### Agent Memory

三层记忆分别为 Redis AOF 的短期消息/状态、Redis 的规则记忆，以及 ChromaDB+BGE-M3 的显式长期语义记忆。所有用户级操作必须带 `user_id`；正式部署中该值应来自认证系统。

#### 写入长期记忆

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agent/memory/long-term \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo-user","content":"我偏好 Spark SQL。"}'
```

#### 写入用户规则

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/agent/memory/rules/prefer-spark \
  -H "Content-Type: application/json" \
  -d '{"scope":"user","user_id":"demo-user","content":"分析任务优先使用 Spark SQL。","priority":500}'
```

- `POST /api/v1/agent/memory/long-term`：显式写入用户长期记忆。
- `GET /api/v1/agent/memory/long-term/{user_id}`：列出长期记忆。
- `DELETE /api/v1/agent/memory/long-term/{user_id}/{memory_id}`：删除长期记忆。
- `PUT /api/v1/agent/memory/rules/{rule_id}`：新增或更新 global/user 规则。
- `GET /api/v1/agent/memory/rules?user_id=...`：合并读取全局和用户规则。
- `DELETE /api/v1/agent/memory/rules/{rule_id}`：删除规则。
- `GET /api/v1/agent/sessions/{session_id}/state?user_id=...`：读取最近 Agent state。
- `DELETE /api/v1/agent/sessions/{session_id}?user_id=...`：清理短期记忆和 checkpoint。

长期记忆默认不从普通对话自动提取，只有显式 API 写入，避免错误事实和隐私信息被静默固化。

### `POST /api/v1/agent/chat/stream`

请求参数与非流式智能体接口一致。事件顺序为 `metadata`、一个或多个 `token`、`result`、`done`。

### `DELETE /api/v1/agent/sessions/{session_id}`

清除指定用户会话的 Redis 短期消息、摘要和最近 Agent checkpoint。Docker 多实例部署时，其他 backend 实例也会立即看到清理结果。

## 自然语言转 SQL（Text2SQL）

接口只返回只读单语句 SQL。服务会拒绝 DDL/DML、注释、`SELECT INTO` 和多语句，并自动追加或收紧 `LIMIT 500`。项目不连接或执行用户业务数据库。

### `POST /api/v1/text2sql`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/text2sql \
  -H "Content-Type: application/json" \
  -d '{
    "question": "统计最近7天活跃用户",
    "engine": "hive",
    "schema_context": "dwd_user_behavior_detail(user_id bigint, dt string)",
    "use_rag": false
  }'
```

支持的引擎：`hive`、`spark_sql`、`mysql`、`clickhouse`。响应包含 SQL、解释、优化建议、置信度、校验结果和 Token 使用量。

## SQL 审核

### `POST /api/v1/sql-review`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sql-review \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT * FROM dwd_order_detail",
    "engine": "spark_sql",
    "include_llm_explanation": true
  }'
```

响应包含风险等级、质量分数、问题列表、优化建议和可选大模型解释。

### `POST /api/v1/sql-review/generate-and-review`

在单个接口中先执行 Text2SQL，再审核生成的 SQL。

## 数仓设计

### `POST /api/v1/warehouse-design`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/warehouse-design \
  -H "Content-Type: application/json" \
  -d '{
    "requirement": "设计电商订单分析数仓",
    "use_rag": true,
    "rag_collection_name": "knowledge_base"
  }'
```

响应包含 ODS、DWD、DWS、ADS、维度表、事实表、指标、关系、DDL、数据流和设计建议。

## 状态码

| 状态码 | 说明 |
| --- | --- |
| `200` | 请求成功 |
| `201` | 文档创建成功 |
| `400` | 文件类型或业务参数错误 |
| `404` | 文档或资源不存在 |
| `422` | Pydantic 参数校验失败 |
| `500` | 智能体或向量库内部错误 |
| `503` | Embedding 模型加载或推理不可用 |
| `502` | 大模型 Provider 调用失败 |
