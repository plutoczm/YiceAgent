# SupportOps AI 部署发现

## 项目分析

- 仓库为 Python 应用，主要目录包括 `app/`、`tests/`、`evals/`、`migrations/`、`docs/`。
- 根目录存在 `README.md`、`pyproject.toml`、`Dockerfile`、`docker-compose.yml`、`Makefile`、`alembic.ini`。
- 存在 PostgreSQL/Alembic 迁移、Redis 集成测试和 Qdrant 集成测试；具体运行要求待配置内容确认。
- 初始 Git 状态：`main...origin/main`，未发现用户已有的已跟踪文件改动；仅本次获准创建的 3 个规划文件处于未跟踪状态。
- Python：`pyproject.toml` 要求 `>=3.11`；Docker 与 GitHub Actions 均使用 Python 3.11；Ruff target 为 `py311`。
- Node.js：未发现 Node/npm/pnpm/yarn 配置或前端构建步骤，当前项目不需要 Node.js。
- 核心依赖：FastAPI、Uvicorn、SQLAlchemy、Alembic、psycopg、Redis、MCP、PyJWT、HTTPX、OpenTelemetry、Prometheus。
- 开发依赖组 `dev`：pytest、pytest-cov、Ruff；RAG 可选组 `rag`：qdrant-client。
- Dockerfile 安装 `.[rag]`，启动时先执行 `alembic upgrade head`，再启动 `uvicorn app.main:app --host 0.0.0.0 --port 8000`。
- Compose 默认服务：API、PostgreSQL 17 Alpine、Redis 7.4 Alpine；Qdrant v1.18.2 使用 `rag` profile，默认 `docker compose up` 不会启动它。
- 本地 `.env.example` 默认使用 SQLite、local reliability、local knowledge、deterministic embedding；多数外部密钥留空即可离线运行。
- Makefile：`make check` 验证 Compose 配置并 compileall；`make test` 执行覆盖率并要求 `>=75%`；`make up` 启动 Compose。
- CI 额外执行 Ruff、PostgreSQL 迁移契约、离线评估、Redis/Qdrant 集成测试和容器构建；CI 初始 `.[dev]` 后在 Qdrant 集成前另装 `.[rag]`。
- 应用入口为 `app.main:app`；健康接口 `/health`，Swagger `/docs`，Prometheus 指标 `/metrics`。
- 默认本地认证为 `AUTH_MODE=dev`，不需要 JWT secret。启用 JWT 时需要 `AUTH_ISSUER`、`AUTH_AUDIENCE`、`AUTH_JWKS_URL`，算法默认 RS256。
- LLM 只有同时配置 `LLM_BASE_URL` 与 `LLM_MODEL` 才启用；API key 可按兼容服务需要填写。默认可离线运行。
- Qdrant 实际接入要求 `KNOWLEDGE_BACKEND=qdrant`、`EMBEDDING_BACKEND=openai`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`；默认 deterministic embedding 不能搭配 Qdrant 应用后端。
- 数据库迁移通过 `DATABASE_URL` 连接；Compose API 启动命令自动执行 `alembic upgrade head`。
- Redis 集成测试需 `RUN_REDIS_INTEGRATION=1`；Qdrant 集成测试需安装 `.[rag]` 并设置 `RUN_QDRANT_INTEGRATION=1`。
- 本地离线运行没有必须提供的秘密值；Compose 内置演示数据库凭据仅适用于本地。生产外部 LLM/embedding、JWT/JWKS、Qdrant API key 与 OTLP 均为按功能启用的可选配置。

## 本地环境

- `python`/`python3` 当前只解析到 `C:\Users\19567\AppData\Local\Microsoft\WindowsApps\` 的 Microsoft Store 占位别名；版本检查退出码为 1，不能作为解释器使用。
- `pip`、`pip3` 与 `py` 启动器不可用；标准 Python 注册表项未找到。
- 发现 Conda：`D:\Anaconda3\condabin\conda.bat`；基础解释器版本待核验。
- Conda base：Python 3.13.9、pip 25.3。
- 多个既有 Conda 环境提供 Python 3.11.15，可用于创建与 CI 一致的标准 venv；无需安装新 Python。
- Docker 客户端/Engine 29.6.1 可用，服务端为 Docker Desktop 4.82.0，Linux 引擎正在运行。
- Docker Compose v5.3.0 可用。
- Git 2.55.0.windows.3 可用；分支为 `main`，与 `origin/main` 对齐，只有本次规划文件未跟踪。
- `D:\Projects\supportops-ai\.venv` 与 `D:\Projects\supportops-ai.venv` 当前均不存在。
- 经用户确认改用命名 Conda 环境：`supportops-ai`，实际路径 `D:\Anaconda3\envs\supportops-ai`。
- 新环境 Python 3.11.15、pip 26.1.2；激活后首个 Python 为 `D:\Anaconda3\envs\supportops-ai\python.exe`。

## 配置与运行发现

- 在 Conda 环境中成功安装 editable `.[dev,rag]`；`pip check` 无依赖损坏。
- 关键版本：FastAPI 0.141.1、SQLAlchemy 2.0.52、Alembic 1.19.1、psycopg 3.3.4、Redis 7.4.1、Qdrant Client 1.19.0、pytest 8.4.2、pytest-cov 6.3.0、Ruff 0.16.4。
- editable 项目定位为 `D:\Projects\supportops-ai`；setuptools 在仓库生成未跟踪的 `supportops_ai.egg-info/`。
- pip 的 Scripts PATH 警告仅发生于用绝对解释器路径、未激活环境的安装进程；激活 Conda 环境即可正常解析命令。
- 已从 `.env.example` 创建被 Git 忽略的 `.env`，44 个配置键完全一致；LLM、embedding、Qdrant API key 均为空。
- 当前 `.env` 支持安全的本地离线运行；真实 LLM/Qdrant 检索需后续填写兼容服务地址、模型和密钥。
- Phase 6 预检：Compose `rag` profile 配置有效，项目当前无容器；但 `datacopilot-backend` 正占用主机端口 8000，与 SupportOps API 的 `8000:8000` 冲突。
- 经用户授权将 Compose API 主机端口改为 8002；容器内部仍为 8000。
- 首次 Compose 构建成功，API、PostgreSQL、Redis、Qdrant 均已启动；PostgreSQL/Redis 健康检查通过，API 日志显示 Alembic 0001/0002 已迁移且 Uvicorn 启动完成。
- 主机 `curl` 首次访问 API/Qdrant 得到 502，与容器状态矛盾；系统存在代理变量，待禁用代理复核。
- 使用 `httpx(trust_env=False)` 复核：API `/health` 与 Qdrant `/readyz` 均 HTTP 200，确认 502 来自本机代理。
- API 健康响应显示 `reliability_backend=local`：`.env` 的 `RELIABILITY_BACKEND=local` 和 localhost Redis URL 覆盖了 Compose 的 Redis 默认值，导致 API 未实际使用 Redis。
- 经授权将 Compose API 固定为 `RELIABILITY_BACKEND=redis`、`REDIS_URL=redis://redis:6379/0`；API 重建后健康接口返回 backend `redis`。
- Phase 6 最终状态：API、PostgreSQL、Redis、Qdrant 全部运行；PostgreSQL/Redis healthy，API 与 Qdrant HTTP 探针成功。
- Phase 7：Alembic 当前为 `20260814_0002 (head)`；`current --check-heads` 与 `alembic check` 均通过，无 schema 漂移。
- PostgreSQL public schema 表：action_receipts、alembic_version、audit_events、orders、pending_actions、ticket_events、tickets。
- Phase 8：Compose 配置、compileall、Ruff 均通过；普通 pytest 为 60 passed、2 skipped。
- 覆盖率测试通过：60 passed、2 skipped，总覆盖率 81.54%，门槛 75%。
- Qdrant 集成测试启用后 2 passed；使用进程级 NO_PROXY 规避本机代理。
- Redis 集成测试硬编码连接 `127.0.0.1:6379`，当前 Compose Redis 未发布主机端口；建议仅绑定 localhost 后执行。
- 经授权为 Redis 添加 `127.0.0.1:6379:6379` 映射；仅本机可访问，容器 healthy，宿主机 PING 成功。
- Redis 集成测试启用后 1 passed；API 在 Redis 重建期间保持运行并继续报告 backend `redis`。
- Phase 9：`/health`、`/docs`、`/openapi.json`、`/metrics` 均 HTTP 200；Swagger UI 标记存在，OpenAPI 标题 SupportOps AI、版本 0.7.0、9 个路径。

## 后续：LLM / Qdrant embedding 配置

- 2026-08-24 用户说明 LLM 已配置，要求补齐 Qdrant、embedding 等剩余配置。
- 当前 `docker compose --profile rag ps` 无项目容器；配置后需重新构建/启动并验证。
- 脱敏审计：LLM endpoint 为 DeepSeek `/v1`，模型与 API key 已配置；未记录或输出密钥内容。
- 当前仍为 `KNOWLEDGE_BACKEND=local`、`EMBEDDING_BACKEND=deterministic`，embedding URL/model/key 均未配置。
- `docker-compose.yml` 未向 API 传递 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，因此 `.env` 中的 LLM 配置当前不会进入 Docker API。
- `.env` 的 Qdrant URL 指向宿主机 `127.0.0.1:6333`；切换 Compose Qdrant 时必须使用容器服务名 `http://qdrant:6333`。
- DeepSeek 官方 API 文档当前仅列出 Chat Completions、Responses、FIM 及 V4 Flash/Pro 模型，未提供 embedding endpoint/model；不能复用 DeepSeek 作为 embedding provider。
- 本机 Ollama 0.32.15 正常运行，已有 `qwen3-embedding:0.6b`（639 MB），无需下载。
- Ollama 官方模型页说明 Qwen3 Embedding 面向多语言文本检索；本机模型元数据 embedding dimension 为 1024。
- 实测 `http://127.0.0.1:11434/v1/embeddings` 返回 OpenAI-compatible 响应，中英文两条输入均得到 1024 维向量，符合项目解析器。
- 推荐 Docker embedding URL 为 `http://host.docker.internal:11434/v1`，宿主机直接运行使用 `http://127.0.0.1:11434/v1`。
- 已配置 `.env` 使用 Qdrant + Ollama OpenAI-compatible embedding，模型 `qwen3-embedding:0.6b`、维度 1024。
- Compose 已补充 LLM 变量传递，容器 embedding URL 使用 `host.docker.internal`，Qdrant URL 使用服务名。
- 本机 embedding 首次实测约 12 秒，已将 `EMBEDDING_TIMEOUT_SECONDS` 配为 60，避免默认 5 秒冷启动超时导致 sparse fallback。
- PostgreSQL、Redis、Qdrant 已按顺序启动；PostgreSQL/Redis healthy，Qdrant ready。
- 临时 API 容器已验证可从 Docker 网络访问宿主机 Ollama：HTTP 200、向量维度 1024；同时可访问 Qdrant readyz。
- 正式 API 已启动并健康；容器内脱敏 Settings 确认 LLM enabled、knowledge backend qdrant、embedding backend openai、模型 qwen3-embedding:0.6b、维度 1024。
- Qdrant `supportops_knowledge` collection 状态 green，已同步 10 个 points，payload 包含文档、chunk、版本、source 元数据。
- collection 使用命名向量 `dense`，1024 维、Cosine；10 个点低于 HNSW indexing threshold 10000，因此 `indexed_vectors_count=0` 且使用全量扫描，属正常行为。
- 真实向量查询中 `KB-REFUND-01` 以 0.716302 排名第一，Qdrant dense retrieval 正常。
- 端到端知识请求返回 HTTP 200、intent knowledge、引用 `KB-ADDRESS-01` 与 `KB-SHIPPING-01`，包含版本、chunk、source URI 与得分。
- Prometheus 指标确认 embedding success、Qdrant startup sync success、Qdrant dense search success、LLM route success。
- 最终 API、Qdrant、Ollama HTTP 探针均 200；Compose config 有效，API 日志无异常。

## 后续：本地用户聊天与客服工单界面

- 2026-08-24 用户明确要求新增本地可用的用户聊天和客服工单界面。
- 当前仓库没有前端或静态资源目录；计划由现有 FastAPI 直接托管纯 HTML/CSS/JavaScript，避免新增 Node.js 构建链。
- 保留现有后端 API、认证和安全流程；UI 仅作为 dev auth 模式下的本地操作入口。
- 客户 API 契约：`POST /v1/support/messages`、`POST /v1/actions/{id}/confirm`、订单/工单读取；使用 `X-Principal-Id`、`X-Customer-Id`、`X-Roles=customer`。
- 客服 API 契约：工单列表、指派、状态流转与审计；使用 `X-Roles=support_agent`。
- 工单合法流转由后端强制执行；UI 仅展示当前状态允许的目标状态，不绕过服务端校验。
- UI 将挂载在 `/ui/`，根路径 `/` 跳转到该页面；Swagger `/docs` 保持不变。
- 已实现纯静态单页 UI：客户聊天、快捷问题、RAG 引用、敏感操作确认、人工工单提示、客服队列、指派和状态流转。
- 静态资源由 FastAPI `/ui` 托管，资源加入 setuptools package-data；无需 Node.js 运行时或独立前端容器。
- UI 定向测试 2 passed；全量测试 62 passed、2 skipped，覆盖率 81.71%，高于 75% 门槛。
- Python compileall、Ruff、JavaScript syntax 和 git diff check 均通过。
- Docker API 镜像已重建并包含静态资源；API、PostgreSQL、Redis、Qdrant 均运行，数据库与 Redis healthy。
- 最终 HTTP 验证：根路径 307 到 `/ui/`，页面/CSS/JS 均 200，健康接口与客服工单 API 均 200；API 日志无错误。
- 当前工单队列为 0 条；可在用户聊天区使用“创建人工工单”快捷问题生成演示工单，再切换客服区处理。

## 后续：双向人工客服会话

- 2026-08-25 用户确认实现客服回复用户的完整链路。
- 设计：新增 `conversation_messages` 表，保存 customer / assistant / agent 消息、作者、会话、客户、时间和用于 UI 恢复的安全响应元数据。
- 客户读取 API 必须按 `customer_id` 限制；客服读取/发送 API 必须从 ticket 获取会话与客户，避免客户端自行指定跨客户会话。
- 现有 UI 的工单“处理备注”仍保留为内部状态备注；新增独立回复输入框与会话记录区域。
- 实施采用 ticket 专属消息表而非通用聊天表：客服仅能看到被转交工单的内容，客户后续回复会进入人工队列而不再调用 AI。
- 客服回复必须先指派给该客服；回复后状态转为 `pending_customer`，客户回复后转回 `assigned`；closed/resolved 工单拒绝新消息。
- 客户页面会按当前会话恢复本人最近工单，避免页面刷新后丢失人工接管状态；跨客户恢复/读取/写入均返回 404。
- Alembic 新迁移 `20260825_0003` 已在 SQLite 契约库和实际 PostgreSQL 中升级至 head；`alembic check` 无 schema 漂移。
- 最终回归：64 passed、2 skipped，覆盖率 82.47%；Ruff、Python compileall、JavaScript syntax 与 git diff check 全部通过。
- Docker API 已重建；OpenAPI 暴露客户消息 GET/POST、客服消息 GET/POST、会话工单恢复 GET；UI 脚本包含“打开会话 / 回复”和“发送人工回复”。
- 最终服务状态：API、PostgreSQL、Redis、Qdrant 均运行，PostgreSQL 与 Redis healthy。

## 发布准备

- Git remote `origin` 的 fetch/push URL 均为 `https://github.com/plutoczm/supportops-ai.git`，当前分支为 `main`。
- 远程 README 仍反映原始 CLI/API 项目状态，未描述本地工作台、Ollama embedding、Qdrant profile、双向人工工单消息或端口 8002。
- 发布范围将包含源码、迁移、测试、README/正式 docs、Compose 和 packaging 配置；不包含 `.env`、agent 规划记录和 `supportops_ai.egg-info/`。
- README 已增加本地工作台、双向人工会话、Windows/Docker 启停、端口 8002，以及不含密钥的 Ollama + Qdrant 配置样例。
- `docs/ARCHITECTURE.md` 已同步 `ticket_messages` 的持久化/隔离/状态流转；`docs/DEMO.md` 已增加人工接管演示；Makefile 已使用 `rag` Compose profile。
- 已创建并推送提交 `65e91c8 feat: add local support workspace and human handoff messaging` 至 `origin/main`。
- 远程 `refs/heads/main` 和本地 HEAD 均为 `65e91c84b0756ae0e5bac8e1558e0f288f2e4c13`。
- 本地未上传文件：findings.md、progress.md、task_plan.md（agent 规划记录）和 supportops_ai.egg-info/（editable 安装元数据）。
