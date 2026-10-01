# WorkflowOps

**Workflow-first 客服与业务分析工作台 · 实习面试演示项目**

用模拟业务数据串起客户服务、受控退款/工单、快照分析和运行追踪。
一个产品、一个最终 Git 仓库、三个职责明确的服务；不是三个自治 Agent。

## 当前状态

2026-10-01：**P1–P4 已验证完成：安全导入、模拟数据、固定客服工作流、不可变交付与受控模板报表；P5–P6 未开始。**
三服务源码快照、隔离分派、doctor 与 Contract 检查已具备；P3 已通过真实 HTTP 与 PostgreSQL 的确认、审批及人工工单闭环。证据见 [P3 验证记录](docs/P3_VALIDATION.md)。根 Git 为独立仓库；P3 验证与复审后按用户授权创建本地提交，不 push。
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

完整 v1 目标：模拟数据 → 客户查询/申请 → 确认或人工接管 → 数据快照 → 运营报表 → 运行记录。
已实现的 offline 流程不调用 LLM，使用实际 API、业务库、独立校验的快照与实际 DuckDB SQL 结果。P4 实测证据见 [P4 验证记录](docs/P4_VALIDATION.md)。
已有语言模型能力作为可选 live 路径保留，不把自主规划或新模型下载作为完成条件。

## 实施顺序

P1 安全目录与源码导入 → P2 模拟数据 → P3 固定流程与客服 → P4 交付与报表 → P5 统一工作台 → P6 验证与面试材料。
完成 Spec 的 AC-01–AC-12 后即冻结演示版，不自动追加平台化阶段。
P4 到此停止。P5–P6 须另行指定；未实现统一 UI 或 P6 全套演示验收。

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

## P2 数据准备入口

```text
conda run -n agentopshub python scripts/dev.py seed-demo --seed 42
conda run -n agentopshub python scripts/dev.py test-service supportops --group demo-data
conda run -n agentopshub python scripts/dev.py test-service datacopilot --group demo-data
```

生成器与导入器分别使用 DataCopilot、SupportOps 环境。相同 fixture 返回 no-op；显式 `seed-demo --seed 42 --reset-demo` 仅重置核验归属后的专用演示库。首次准备专用 PostgreSQL 可能需要固定 Docker 镜像；不操作原项目库。运行配置与密码仅存 ignored `.runtime/`，不要将其纳入 Git。当前有 24 个真实导入的模拟订单；已提供 P3 融合 HTTP API，统一 UI 待 P5。完整证据见 [P2 验证记录](docs/P2_VALIDATION.md)。

## P3 离线 HTTP API

```text
conda run -n agentopshub python scripts/dev.py demo-up --mode offline
conda run -n agentopshub python scripts/dev.py test-service harness --group workflow -q
conda run -n agentopshub python scripts/dev.py test-service supportops --group safety -o addopts= -q -rs
conda run -n agentopshub python scripts/dev.py test-integration --group support
conda run -n agentopshub python scripts/dev.py demo-down
```

前置为已准备并运行的 P2 自有 PostgreSQL。当前启动 Harness `127.0.0.1:8010`、SupportOps `127.0.0.1:8011` 与 DataCopilot `127.0.0.1:8012` 的 offline API；统一 UI 待 P5。不安装依赖或加载模型。停机只停止本次自有 HTTP 进程，保留数据库。集成测试使用独立 PostgreSQL schema，可重复执行，不重置主演示库；需要通过上述测试入口运行，避免直接将集成测试指向主演示库。

先向 Harness `POST /api/demo/session` 提交 `{"persona":"customer-a1"}` 并保留 HttpOnly cookie；白名单身份为 customer-a1、customer-a2、operator-a、customer-b1，切换撤销旧 session。这是本地演示身份选择。

再调用 `POST /api/workflows/<workflow_id>/runs`，请求为 `{"payload":{...},"parent_run_id":null}`。已实现五种 workflow：

| workflow_id | payload 示例/动作 |
|---|---|
| support_lookup | `{"object_id":"ORD-1001"}`；或 `{"kind":"operation","object_id":"<operation_id>"}` |
| support_message | `{"action":"refund","conversation_id":"demo-low","order_id":"ORD-1001"}`；action=message 支持 message，action=handoff 创建工单 |
| support_confirm | `{"action_id":"<pending_action_id>","confirm":true}`；false 取消 |
| support_review | `{"operation_id":"<operation_id>","decision":"approve","expected_version":1}`；仅运营，版本取实际 operation |
| ticket_action | action 为 list/get/messages/reply/assign/transition/operation；reply 带 message，transition 带 status，除 list 外带 ticket_id |

返回 run_id、实际 steps、domain_refs、终态和 typed result/error。`GET /api/runs/<run_id>` 返回权限过滤的元数据，不返回消息或结果行。等待确认/审批后本次 run 结束；下一人工动作创建新 run，可关联 parent_run_id。写入未知时核对 domain_refs 中已知 operation_id，不能自动重放。确认请求即使省略 parent_run_id，也按领域契约返回 operation_id=action_id。派发前/只读失败标记 failed；只有写请求已开始且结果不确定时标记 unknown_outcome。已实现机器契约见 [P3 API schema](contracts/service_api/v1/p3.json)，实测证据和当前演示库状态见 [P3 验证记录](docs/P3_VALIDATION.md)。

## P4 快照与模板报表

```text
conda run -n agentopshub python scripts/dev.py check-contracts
conda run -n agentopshub python scripts/dev.py test-service datacopilot --group governed -q -rs
conda run -n agentopshub python scripts/dev.py test-integration --group delivery
conda run -n agentopshub python scripts/dev.py test-integration --group reports
```

在 Harness 选择 operator-a 后，调用 `refresh_snapshot`，payload 为 `{}`；再调用 `analytics_report`，payload 为 `{"report_id":"orders_total"}`。另外四个固定 ID 为 refund_requests_by_status、pending_approval_count、tickets_by_status、overdue_open_tickets。原五个客服 workflow 保持可用，当前共七个固定 workflow。

刷新由运营显式触发；业务提交与分析刷新分别返回各自 run。首次未发布快照时报表明确失败，退款提交后刷新前仍看到旧统计。消费者每个请求固定一个不可变目录，独立校验后执行真实 SQL；只允许报表 ID，不接受租户、路径、SQL、policy 或 as_of。结果显示 template/v1、真实 rows/row_count、snapshot_id、delivery_fingerprint、生成时间、水位、服务端 as_of 和耗时，不返回路径或 SQL。旧快照保留，没有归档/自动清理策略。原 live Text2SQL/JWT 入口保持原样，P4 未调用真实模型。

当前机器契约见 [P4 API schema](contracts/service_api/v1/p4.json)，实测结果、原有 skip 与边界见 [P4 验证记录](docs/P4_VALIDATION.md)。
