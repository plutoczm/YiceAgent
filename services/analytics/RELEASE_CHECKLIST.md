# 发布检查清单

## 仓库

- [ ] 不存在误提交的密钥、日志、缓存和模型文件。
- [ ] 根目录 `.env` 未被提交，Docker 生产默认文件不含真实 Key。
- [ ] README、变更记录和版本号已更新。
- [ ] 新增说明文档和注释均为中文。

## 后端

- [ ] `/health` 返回 `ok`。
- [ ] `/health/live` 返回 `ok`。
- [ ] `/docs` 可以打开且接口说明完整。
- [ ] DeepSeek 或 Ollama Provider 健康检查通过。
- [ ] 默认 `knowledge_base` 集合可以创建和查询。
- [ ] `python scripts/verify_bge_m3.py` 输出 1024 维真实 BGE-M3 向量。
- [ ] 工具 JSON Schema 可通过 `/api/v1/agent/tools` 查看。

## 前端

- [ ] 智能问答、知识库、Text2SQL、SQL 审核和数仓设计页面可用。
- [ ] SSE 响应能够增量显示。
- [ ] 中文内容在桌面和移动宽度下无溢出。

## Agent 与 RAG

- [ ] 意图路由和多步骤 SQL 工作流正确。
- [ ] 短期记忆、摘要、最近 Agent state 和清理接口正确。
- [ ] 长期记忆和规则记忆按 `user_id` 隔离，显式写入/删除接口正确。
- [ ] 向量与混合检索模式均可用。
- [ ] 无召回或低分结果触发拒答。
- [ ] 引用包含文档名、分块位置和相似度。

## Docker 部署

- [ ] `docker compose config --quiet` 通过。
- [ ] 后端、前端、ChromaDB 和 Redis 健康检查通过。
- [ ] Redis AOF 文件已落在 `data/redis/`，backend 重启后会话状态仍可读取。
- [ ] BGE-M3 缓存位于 `models/` 且未提交 Git。
- [ ] 可选 Ollama profile 可以启动。
- [ ] 数据目录已经持久化并具备正确权限。

## 测试

```bash
python -m pytest -q
python -m pytest --cov=backend --cov=frontend --cov-report=term-missing
```

- [ ] 全量测试通过。
- [ ] 覆盖率达到项目门槛。
- [ ] 测试不依赖真实外部 API。

## 演示

- [ ] 准备一份知识库文档并完成问答。
- [ ] 演示 Text2SQL 后自动进入 SQL 审核。
- [ ] 演示数仓分层设计。
- [ ] 展示 Swagger、工具 Schema、测试结果和 Docker 服务状态。
