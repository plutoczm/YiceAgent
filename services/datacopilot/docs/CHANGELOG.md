# 变更记录

## 当前开发版本

### 2026-08-24：生产化与记忆系统更新

- 默认 Embedding 切换为真实 FlagEmbedding `BAAI/bge-m3`，支持 lazy load、CPU/GPU 设备选择、FP16 控制和项目内模型缓存。
- 新增 `scripts/verify_bge_m3.py`，用于首次下载、预热和验证 1024 维真实向量输出。
- Docker backend 使用 PyTorch 官方 CPU wheel，避免 CPU 部署引入 CUDA 运行库。
- Docker Compose 新增 Redis AOF 服务；短期消息、摘要、最近 Agent state 和规则记忆支持跨 backend 实例共享。
- 新增长期记忆和规则记忆 API；长期记忆通过 ChromaDB+BGE-M3 按 `user_id` 语义召回，默认仅允许显式写入。
- Docker backend 可选加载根目录 `.env`；修复根目录 `DEEPSEEK_API_KEY` 被 Compose 空变量覆盖的问题。
- 文档、架构图、部署与 API 示例同步更新。

### 验证

- 全量自动化测试：`212 passed`。
- BGE-M3 真实 CPU 推理：1024 维、L2 norm 1.0。
- Docker backend/frontend/chromadb/redis 健康检查通过；backend 与 Redis 重启后记忆状态可恢复。

### 智能体

- 使用 LangChain `StructuredTool` 注册 RAG、Text2SQL、SQL 审核和数仓设计工具。
- 为所有工具增加 Pydantic JSON Schema 参数校验和工具目录接口。
- 增加会话短期记忆、旧消息摘要压缩和会话清理。
- 增加 LangGraph 最大执行步数，防止循环调用。
- SSE 改为增量分片输出。
- 新增 `validate_result` 结果校验节点：SQL 结构/引擎校验、SQL 审核结论与规则引擎一致性核对、数仓分层覆盖率与 DDL 检查、RAG 引用与回答检查，校验摘要透出到 `metadata.validation`。

### 多 LLM 智能路由

- 新增通用 OpenAI 兼容 Provider，同时服务 OpenAI 云端与 Ollama 本地 OpenAI 兼容接口。
- 新增 `TaskBoundLLMProvider` + `RoutingLLMProvider`：专业数据工程任务（Text2SQL/SQL 审核/数仓设计）路由到本地微调模型，通用需求走云端，本地故障自动降级。
- 新增 `LocalModelSettings` 与 `DATACOPILOT_LLM__ROUTING_*` 配置，默认关闭路由，行为与旧版本一致。

### RAG 检索

- 新增纯向量与混合检索模式。
- 混合检索使用稠密 ANN、BM25、RRF 融合和轻量词项重排。
- 保留分数阈值、无召回拒答和引用来源。
- 首次启动时幂等创建默认知识库集合。

### 大模型与部署

- 新增 Ollama Provider，可与 DeepSeek 通过环境变量切换。
- Docker Compose 增加 Ollama Provider 配置。
- 新增意图准确率、Precision@K、MRR 和工具成功率评测模型。
- 完成说明文档、代码注释和 OpenAPI 文案中文化。

## 0.1.0

- 建立项目骨架、配置系统和结构化日志。
- 实现知识库 RAG、ChromaDB 和 DeepSeek Provider。
- 实现 FastAPI、Text2SQL、SQL 审核和数仓设计。
- 实现 LangGraph 路由、Streamlit 前端和 Docker Compose。
- 建立单元、接口、基础设施、部署和集成测试。
