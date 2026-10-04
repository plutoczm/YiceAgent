# Harness — WorkflowOps Monorepo

职责：统一入口、固定工作流、typed HTTP 适配、运行元数据。

- 目录：`services/harness`；保留的 CLI/内部服务标识：`harness`。
- 原环境：`agentopshub`；解释器由根启动器调用，不复制/合并环境。
- 子进程 cwd 是本目录，PYTHONPATH 是本目录下 `backend/src`；仅监听 `127.0.0.1:8010`。
- 默认入口是根目录的 `start.cmd` / `stop.cmd`；回归为根 `scripts/dev.py verify --offline` 和 `demo-check --all`。
- Business Contract 发布源在根 `contracts/business_data/v1`；producer/consumer 保留受检查副本，数据交付使用原 Delivery v2。
- `config/upstream.env.example` 是审核后的原项目参考模板，未配置真实密钥，offline 工作台不加载它。
- 本目录的原 README/ROADMAP/Compose、frontend/web/training/评测等历史资产保留为来源参考或非默认能力。不要从这些历史说明自动安装依赖、下载模型、启动另一套业务库或部署堆栈。

当前完整运行和服务边界见 [Monorepo 指南](../../docs/MONOREPO.md)。来源/文件哈希见 [整合清单](../../docs/consolidation/file-manifest.json)，原 LICENSE 保留。目录调整仅为路径整理，没有改变业务逻辑、接口、认证/审批、SQL 门禁或技术栈。
