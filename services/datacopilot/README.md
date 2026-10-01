# DataCopilot

面向数据工程场景的 AI 智能体平台（运行时服务名：DataPilot-AI）。

DataPilot-AI 将知识库 RAG、Text2SQL、SQL 审核、数仓设计和 LangGraph 智能体路由整合到同一个应用中，后端使用 FastAPI，前端使用 Streamlit，并支持 Docker 部署与本地模型。

启停方式、Docker 部署和访问地址请参阅 [START.md](START.md)。

## 系统架构

```mermaid
flowchart LR
    UI[Streamlit 前端] --> API[FastAPI 接口层]
    API --> Agent[LangGraph 智能体]
    Agent --> RAG[RAG 服务]
    Agent --> T2S[Text2SQL]
    Agent --> Review[SQL 审核]
    Agent --> WH[数仓设计]
    RAG --> VS[向量库端口]
    RAG --> LLM[大模型端口]
    T2S --> LLM
    Review --> LLM
    WH --> LLM
    VS --> Chroma[ChromaDB]
    Agent --> Redis[Redis AOF 记忆]
    Chroma --> BGE[BGE-M3 Embedding]
    LLM --> DeepSeek[DeepSeek]
    LLM --> Ollama[Ollama 本地模型]
```

项目采用整洁架构：

- 表现层：FastAPI 路由、Pydantic 接口模型和 Streamlit 页面。
- 应用层：RAG、Text2SQL、SQL 审核、数仓设计和智能体工作流。
- 领域层：文档、分块实体以及大模型、向量库端口。
- 基础设施层：ChromaDB、DeepSeek、Ollama、文档加载器和 Embedding 实现。

## 主要功能

- 支持 TXT、Markdown、PDF 和 DOCX 的知识库 RAG。
- 使用 ChromaDB 持久化向量，支持元数据过滤。
- 默认采用稠密向量 + BM25 混合召回、RRF 融合和轻量重排。
- 默认使用 FlagEmbedding 加载真实 `BAAI/bge-m3`，输出 1024 维归一化 dense 向量；模型按需加载并缓存到 `models/huggingface`。
- DeepSeek 异步调用，包含超时、有限重试、流式输出、健康检查和 Token 统计。
- 可切换到 Ollama 本地模型。
- 支持 Hive、Spark SQL、MySQL 和 ClickHouse 的 Text2SQL。
- Managed Business Analytics 在 D1-D4 上增加了 D5 配置型身份验证、单 tenant grant、认证 API 和 Agent 能力；默认未配置生产 IdP 或 tenant grants（[D5 集成边界](docs/TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md)）。
- 提供内部 governed snapshot execution foundation：独立校验 Delivery v2、隔离 tenant snapshot、限制 SQL 结果；[D4 执行边界](docs/GOVERNED_BUSINESS_ANALYTICS_EXECUTION.md)。
- Text2SQL 仅生成只读单语句，拦截 DDL/DML、注释与多语句，并自动将结果限制在 500 行以内。
- SQL 风险评分、规则检查、性能建议和大模型解释。
- 生成 ODS、DWD、DWS、ADS、维度表、事实表、DDL 和指标定义。
- LangGraph 自动意图识别和多步骤工作流，含 `validate_result` 结果校验节点。
- LangChain 结构化工具与 Pydantic JSON Schema 参数校验。
- 会话短期记忆、摘要压缩、最大执行步数和会话清理。
- Docker 生产模式使用 Redis AOF 共享短期消息、最近 Agent state 和规则记忆；ChromaDB+BGE-M3 保存显式长期语义记忆，支持多实例读取。
- 多 LLM 智能路由：专业数据工程任务 → 本地微调模型，通用需求 → DeepSeek/OpenAI 云端，本地故障自动降级。
- 模型微调子系统（`training/`）：基于公开数据集构建 3200+ 条数据工程 SFT 指令集，Unsloth 4-bit QLoRA 微调 Qwen3-8B，基座/微调对比评测，GGUF 导出与 Ollama 私有化部署。
- 后端、前端、ChromaDB、Redis 和可选 Ollama 的 Docker Compose 部署。
- 单元、接口、基础设施和集成测试。
- `evaluation/rag_cases.json` 提供可版本化的离线 RAG 问题集、Top-1 检索和答案引用契约测试。

## 快速开始

Windows 推荐直接使用：

```bat
start.cmd
```

也可以使用 Conda 环境：

```bash
conda env create -f environment.yml
conda activate datacopilot
python scripts/verify_bge_m3.py  # 首次下载并验证真实 BGE-M3，可跳过后按需加载
python manage.py start --no-open
```

首次 BGE-M3 下载约 3 GB；CPU 环境首次推理会比普通 API 启动慢，模型会缓存到 `models/huggingface`。

配置文件为 `.env`，可参考 `.env.example`。默认大模型为 DeepSeek，需要配置：

```text
DEEPSEEK_API_KEY=你的密钥
```

启动后访问：

- 工作台：`http://127.0.0.1:8502`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

## Docker 部署

```bash
# 根目录 .env 存放密钥，先从示例创建
cp .env.example .env
# 填写 .env 中的 DEEPSEEK_API_KEY
docker compose --env-file docker/.env.production up -d --build
docker compose --env-file docker/.env.production logs -f
```

Compose 会加载 `docker/.env.production` 的非敏感默认值，并可选加载根目录 `.env` 到 backend；不要把真实 Key 写进或提交 `docker/.env.production`。

启用 Ollama：

```bash
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull qwen3
```

然后设置 `LLM_PROVIDER=ollama`、`OLLAMA_ENABLED=true` 和 `OLLAMA_MODEL=qwen3` 后重新启动。运行数据统一保存在 `data/` 目录。

## 关键环境变量

```text
DATACOPILOT_ENVIRONMENT=development|test|production
DATACOPILOT_DEBUG=false
DATACOPILOT_LLM__DEFAULT_PROVIDER=deepseek|ollama
DATACOPILOT_LLM__ROUTING_ENABLED=false
DATACOPILOT_LLM__CLOUD_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
DATACOPILOT_OLLAMA__BASE_URL=http://127.0.0.1:11434
DATACOPILOT_OLLAMA__CHAT_MODEL=qwen3
DATACOPILOT_LOCAL__ENABLED=false
DATACOPILOT_LOCAL__BASE_URL=http://127.0.0.1:11434/v1
DATACOPILOT_LOCAL__CHAT_MODEL=datacopilot-qwen3-8b
DATACOPILOT_VECTOR_STORE__MODE=persistent|http
DATACOPILOT_VECTOR_STORE__HOST=127.0.0.1
DATACOPILOT_EMBEDDINGS__DEFAULT_MODEL=BAAI/bge-m3
DATACOPILOT_EMBEDDINGS__DEVICE=auto
DATACOPILOT_EMBEDDINGS__MAX_LENGTH=1024
DATACOPILOT_RAG__CHUNK_SIZE=1000
DATACOPILOT_RAG__CHUNK_OVERLAP=200
DATACOPILOT_AGENT__MAX_STEPS=8
DATACOPILOT_MEMORY__BACKEND=in_memory|redis
DATACOPILOT_MEMORY__REDIS_URL=redis://127.0.0.1:6379/0
DATACOPILOT_MEMORY__SESSION_TTL_SECONDS=86400
DATACOPILOT_MEMORY__LONG_TERM_COLLECTION=agent_long_term_memory
BACKEND_URL=http://backend:8000
```

## 项目结构

```text
backend/app/
  core/                 配置、常量和日志
  domain/               实体与端口
  application/          RAG、Text2SQL、SQL 审核、数仓设计、智能体、评测
  infrastructure/       ChromaDB、DeepSeek、Ollama、加载器、Embedding
  presentation/api/     FastAPI 路由、模型和依赖
frontend/               Streamlit 页面、组件和后端客户端
evaluation/             版本化 RAG 黄金问题集
scripts/                BGE-M3 模型验证等运维脚本
tests/                  单元、接口、基础设施和集成测试
docs/                   中文技术文档
```

## 技术栈

- Python 3.11+
- FastAPI、Pydantic v2、pydantic-settings
- LangGraph、LangChain Core
- ChromaDB、BGE-M3、Redis、混合检索
- DeepSeek、Ollama、httpx
- Streamlit、Docker Compose
- pytest、pytest-cov

## 测试

```bash
python -m pytest -q
python -m pytest --cov=backend --cov=frontend --cov-report=term-missing
```

离线 RAG 质量门禁：

```bash
python -m pytest -q tests/evaluation/test_rag_quality.py
```

最近一次验证（2026-09-26，P0 release-candidate baseline）：`492 passed`。测试数量会随工程演进变化，以本地 `pytest` 输出为准。

首次验证真实模型（会下载约 3 GB 模型文件）：

```bash
python scripts/verify_bge_m3.py
```

## 常见问题

### 知识问答返回 502

通常表示检索已完成、但回答阶段的 LLM 不可用。先检查：

```bash
curl http://127.0.0.1:8000/health
```

若 `llm_provider.api_key_configured=false`，确认根目录 `.env` 已设置 `DEEPSEEK_API_KEY`，然后重建 backend：

```bash
docker compose --env-file docker/.env.production up -d --force-recreate backend
```

## 文档

- [技术栈说明](docs/AGENT_TECH_STACK.md)
- [系统架构](docs/ARCHITECTURE.md)
- [接口参考](docs/API_REFERENCE.md)
- [Managed Business Analytics 边界](docs/BUSINESS_ANALYTICS_BOUNDARY.md)
- [Trusted Business Analytics Integration (D5)](docs/TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md)
- [Business Data Contract consumer](docs/BUSINESS_DATA_CONTRACT_CONSUMER.md)
- [部署指南](docs/DEPLOYMENT.md)
- [面试讲解](docs/INTERVIEW_GUIDE.md)
- [路线图](docs/ROADMAP.md)
- [变更记录](docs/CHANGELOG.md)
- [发布检查清单](RELEASE_CHECKLIST.md)

## Managed Business Text2SQL (D3)

The D3 foundation renders a trusted, scoped Business Data Contract catalog, generates an untrusted SQL candidate through the existing Text2SQL service adapter, and authorizes the candidate with dialect-aware AST and structural checks. It returns a `READY` plan only; it does not execute SQL, ingest SupportOps delivery rows, or connect a production Agent or warehouse. See [the D3 pipeline boundary](docs/MANAGED_TEXT2SQL_PIPELINE.md).

## Governed Snapshot Execution (D4)

D4 adds an internal Delivery v2 consumer, tenant-scoped in-memory snapshot, translated-SQL reauthorization, and bounded `EXECUTED` result. D5 adds the authenticated API and Agent entrypoints around this workflow. No production IdP, RLS, signed producer delivery, or live SupportOps connection is configured. See [the D4 execution boundary](docs/GOVERNED_BUSINESS_ANALYTICS_EXECUTION.md).

## Trusted Business Analytics Integration (D5)

D5 adds a pinned PyJWT RS256/JWKS verification boundary, server-configured subject-to-tenant grants, authenticated managed query and Agent routes, and a deterministic 50-case enterprise analytics evaluation. Generic Text2SQL remains a separate workflow. D5 does not claim production SSO or database RLS. See [the integration boundary](docs/TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md).
