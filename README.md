# WorkflowOps

**Workflow-first 客服与业务分析工作台 · 实习面试演示项目**

用模拟业务数据串起客户服务、受控退款/工单、快照分析和运行追踪。
一个产品、一个最终 Git 仓库、三个职责明确的服务；不是三个自治 Agent。

## 当前状态

2026-10-01：**P1 安全导入与运行边界已验证完成；P2–P6 未开始。**
三服务源码快照、隔离分派、doctor 与 Contract 检查已具备；尚未启动融合服务或实现业务闭环。证据见 [P1 验证记录](docs/P1_VALIDATION.md)。根 Git 已按用户授权独立初始化；P1 基线纳入本地版本控制，不 push。
工作名称 WorkflowOps 可在后续统一改名，不影响架构边界。

## 阅读顺序

1. [产品与架构 Spec](docs/SPEC.md)：范围、职责、流程、技术取舍、接口与验收。
2. [可执行 Plan](docs/PLAN.md)：六个批次、目标文件、步骤、未来验证命令与停止条件。
3. [源码核对记录](docs/SOURCE_AUDIT.md)：三个原仓库状态、环境、关键代码与 Contract 哈希。

## 模块分工

| 模块 | 来源 | 主责 |
|---|---|---|
| services/harness | AgentOpsHub | 固定 Workflow、入口、权限传递、运行记录 |
| services/supportops | supportops-ai | 客服业务规则、确认/审批、工单与业务事实 |
| services/datacopilot | DataCopilot | 模拟数据生成、快照消费、受控 SQL 分析 |

默认演示：模拟数据 → 客户查询/申请 → 确认或人工接管 → 数据快照 → 运营报表 → 运行记录。
offline 模式不调用 LLM，但使用实际 API、实际业务库和实际 SQL，不用硬编码成功响应。
已有语言模型能力作为可选 live 路径保留，不把自主规划或新模型下载作为完成条件。

## 实施顺序

P1 安全目录与源码导入 → P2 模拟数据 → P3 固定流程与客服 → P4 交付与报表 → P5 统一工作台 → P6 验证与面试材料。
完成 Spec 的 AC-01–AC-12 后即冻结演示版，不自动追加平台化阶段。
P1 到此停止。后续另行指定 P2；Plan 中后续命令必须先实现对应脚本才能运行。

## 保护原项目

三个原目录、原 Conda 环境和原数据库本轮均不修改。
SupportOps 未提交/未跟踪的有效代码必须在 P1 审阅保留，不能只取 GitHub HEAD。
无自动 commit/push、无秘密复制、无真实客户/支付、无公网账号系统要求。

## P1 检查入口

```text
conda run -n agentopshub python scripts/dev.py doctor
conda run -n agentopshub python scripts/dev.py check-contracts
conda run -n agentopshub python -B scripts/verify_import.py
```

`test-service <harness|supportops|datacopilot> <pytest 参数>` 分别在对应 Conda 环境和服务 cwd 执行。服务历史文档属于原项目上下文，根 docs/PLAN.md 才是当前阶段依据。
