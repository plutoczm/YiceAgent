# DataCopilot 使用命令速查

## 本地使用

```bat
.\start.cmd          :: 启动（首次自动建 datacopilot conda 环境）
.\stop.cmd           :: 停止
```

跨平台等价命令：

```bash
python manage.py start
python manage.py stop
python manage.py setup     # 只创建/检查环境
```

首次使用真实 BGE-M3：

```bash
python scripts/verify_bge_m3.py
```

- 工作台：http://127.0.0.1:8502
- API 文档：http://127.0.0.1:8000/docs
- 修改 `.env` 后必须重启后端才生效。

## Docker 完整栈

1. 从 `.env.example` 创建根目录 `.env`，填写 `DEEPSEEK_API_KEY`。
2. 启动 FastAPI、Streamlit、ChromaDB 和 Redis：

```bash
docker compose --env-file docker/.env.production up -d --build
docker compose --env-file docker/.env.production ps
```

3. 访问工作台 `http://127.0.0.1:8501`。Docker 模式默认启用 Redis AOF 多实例记忆；BGE-M3 模型缓存复用项目 `models/` 目录。

## Vercel 部署与移除

```bash
npx vercel                                              # 部署
npx vercel ls                                           # 先列出当前部署
npx vercel remove datacopilot-orcin.vercel.app --yes    # 移除，下线

```

部署目录由 `vercel.json` 固定为 `web/`，公网地址：https://datacopilot-orcin.vercel.app

## 切换调用的 LLM

所有 LLM 的 **URL、API Key、模型名** 都在项目根目录 `.env` 里配置（文件内已带中文注释，标了「★ Key 填这里」「URL 填这里」「模型名填这里」），对照 `.env.example` 填写。改完保存 → 重启后端（`python manage.py stop` → `start`）生效。

- **默认云端**：DeepSeek
- **换 provider**：在 `.env` 填好对应组的 Key/URL/Model，并把 `DATACOPILOT_LLM__DEFAULT_PROVIDER` 改成 `deepseek` / `openai` / `ollama` 之一
- **本地微调模型**：已开启智能路由（`DATACOPILOT_LLM__ROUTING_ENABLED=true`），专业任务走本地 `datacopilot-qwen3-4b`，通用需求走云端
