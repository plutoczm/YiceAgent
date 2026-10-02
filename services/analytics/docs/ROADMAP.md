# 项目路线图

## 已完成

- FastAPI 后端、Streamlit 前端和整洁架构分层。
- TXT、Markdown、PDF、DOCX 文档摄取。
- BGE-M3 dense Embedding、ChromaDB 持久化和元数据过滤。
- 稠密向量 + BM25 混合召回、RRF 融合、重排和低分拒答。
- DeepSeek 与 Ollama 大模型 Provider。
- Text2SQL、SQL 审核和分层数仓设计。
- LangGraph 意图路由、多步骤工作流和 LangChain 结构化工具。
- Redis AOF 短期记忆、最近 Agent state、规则记忆，及 ChromaDB+BGE-M3 显式长期记忆。
- Agent 结果校验节点（`validate_result`）：SQL 结构/引擎校验、审核结论一致性核对、数仓分层覆盖率、RAG 引用校验。
- 多 LLM 智能路由：专业数据工程任务 → 本地微调模型，通用需求 → 云端，失败自动降级。
- OpenAI 兼容 Provider：统一 OpenAI 云端与 Ollama 本地 OpenAI 兼容接口。
- Docker Compose、健康检查、资源限制和项目内持久化。
- 单元、接口、基础设施、部署和集成测试。
- DataCopilot D1 = COMPLETE — Workflow / Agent Boundary：建立可信上下文、业务目录 port 和 scope preparation；没有消费 Contract、生成或执行 SQL。
- DataCopilot D2 = COMPLETE — Business Data Contract Consumer：接受并 pin v1 JSON artifact，通过 strict infrastructure adapter 映射 typed catalog；没有读取 Delivery JSONL 或生成/执行 SQL。
- DataCopilot D3 = COMPLETE — deterministic managed Text2SQL plan generation; no query execution.
- DataCopilot D4 = COMPLETE — internal governed snapshot execution foundation.
- DataCopilot D5 = COMPLETE — trusted identity boundary, tenant grants, authenticated managed API/Agent capability, and deterministic 50-case evaluation.
- Recommended next stage after RC review: Production Validation P1 — Push / Pull Request / GitHub CI + Real IdP Deployment Validation.

## 近期规划

### 数据平台集成

- 接入 Hive Metastore 和 Spark Catalog，自动获取真实表结构。
- 接入 ClickHouse system 表和执行计划。
- 支持 Spark、Hive 和 ClickHouse 只读查询沙箱。
- 增加 SQL 血缘和影响分析。

### RAG 与评测

- 使用 Cross-Encoder 或大模型进行精排。
- 引入 Qdrant 稀疏索引，支持 BGE-M3 原生稠密 + 稀疏检索。
- 评估 BGE-M3 sparse/ColBERT 多向量召回与专用向量引擎。
- 建立带标准答案、相关文档和失败样本的版本化评测集。
- 增加答案忠实度、SQL 可执行率、P95 延迟和 Token 成本看板。

### 模型微调与私有化部署

- 基于公开数据集（Spider）转换 + 知识库/规则引擎补齐，构建 3200+ 条数据工程 SFT 指令集。
- 基于 Unsloth 对 Qwen3-8B-Instruct 开展 4-bit QLoRA 微调，产出 LoRA 适配器。
- 基座 vs 微调模型的标准化评测框架（Text2SQL 组件 F1、数仓设计结构覆盖）。
- LoRA 合并、GGUF 量化导出与 Ollama 私有化部署，接入多 LLM 智能路由。

### 平台能力

- 用户认证、工作区和租户隔离。
- 用户认证、工作区与租户隔离后，将 memory `user_id` 绑定到可信身份。
- 大文档异步摄取、任务队列和进度查询。
- OpenTelemetry 调用链、集中日志、限流和审计。

## 中长期探索

- 多智能体主管模式，要求共享状态、无环拓扑、全局深度上限和明确终止条件。
- Kafka/Flink 实时链路诊断助手。
- GPU Embedding、实时向量索引与本地推理优化。
- 人工审批、高风险操作确认和企业权限系统。

规划内容不代表当前已实现能力，当前状态以 README、测试和发布记录为准。
