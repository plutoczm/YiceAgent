# 验收记录

| 主题 | 范围 |
|---|---|
| [基础设施](foundation.md) | 隔离环境、协议、专用 PostgreSQL/Qdrant 与 BGE 下载 |
| [GPU](gpu.md) | 实际 BGE/Reranker GPU 小样本，含失败和回退边界 |
| [业务](business.md) | 双场景、确认审批、真实数据库/HTTP/浏览器；迁移前83测试 |
| [目录迁移](layout.md) | Git 保存、路径重组、当前回归与资源保护 |

前三页是迁移前验收记录，不能把其中历史命令或模型结果称为本次重排执行。当前运行步骤见 [DEVELOPMENT](../DEVELOPMENT.md)。三模型、RAG、长对话、记忆与上下文尚未验收。
