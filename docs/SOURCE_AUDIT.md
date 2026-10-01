# WorkflowOps — 源码与环境核对记录

日期：2026-09-30。来源：通过 Desktop Commander 读取 Windows 本机项目。
用途：为 Spec/Plan 提供可核对的当前实现依据，不是全量代码审计或运行验收报告。
范围：当前轮刷新了 Git/解释器/关键 API/schema/Contract；其余说明结合上一轮已直接读取的核心实现。

## 1. 源仓库快照

| 项目 | 目录 | 分支 | HEAD |
|---|---|---|---|
| AgentOpsHub | D:\Projects\AgentOpsHub | feat/phase7a-knowledge-agent | 5daedb6307f1eea7bd0e3191fcbf02eb3e8d0f97 |
| SupportOps AI | D:\Projects\supportops-ai | main | 90ba9a7ba9f0fd71c4267de6b91f3a68587a32a3 |
| DataCopilot | D:\Projects\DataCopilot | feat/governed-business-analytics | facc6c31eeb902a80ea1d5521f12459f84f124bf |

上述远端相对状态来自本机 tracking ref，没有执行 git fetch，不能当作 GitHub 最新状态。
AgentOpsHub 工作树无短状态条目，本机显示 ahead 1；SupportOps 显示 ahead 3 且有修改/未跟踪文件。
DataCopilot 跟踪分支无 ahead/behind 标记，有未跟踪的 findings.md、progress.md、task_plan.md。

SupportOps 已跟踪修改：`.github/workflows/ci.yml`、`README.md`。
需要特别保留的未跟踪内容：
- `app/business_data/`、`contracts/`、`scripts/`。
- `docs/BUSINESS_DATA_CONTRACT_V1.md`、`BUSINESS_DATA_DELIVERY_V1.md`、`BUSINESS_DATA_EXPORT_V1.md`。
- `tests/test_business_data_contract_v1.py`、`test_business_data_delivery_v1.py`、`test_business_data_export_v1.py`。
- `tests/integration/test_business_data_snapshot_postgres.py` 与相关工作记录。

`supportops_ai.egg-info/` 是生成物，不应因未跟踪而盲目纳入。
完整复制集合与每文件 hash 将由 P1 生成 source-manifest；本记录不冒充完整迁移清单。

## 2. Conda 环境

| 环境 | 实际解释器 | 当前轮查询的 Python |
|---|---|---|
| agentopshub | D:\Anaconda3\envs\agentopshub\python.exe | 3.12.14 |
| supportops-ai | D:\Anaconda3\envs\supportops-ai\python.exe | 3.11.15 |
| datacopilot | D:\Anaconda3\envs\datacopilot\python.exe | 3.11.15 |

上一轮直接读取依赖和安装元数据：
AgentOpsHub 要求 LangGraph >=1.2.11,<1.3（安装 1.2.11）；DataCopilot 要求 >=0.6,<1（安装 0.6.11）。
SupportOps 没有 LangGraph 依赖，其主要业务工作流不依赖该框架。
AgentOpsHub 的 wheel 包和 SupportOps 的应用都叫顶层 `app`；直接混装/同进程导入存在名称冲突。
上一轮三环境的 `pip check` 均未发现声明依赖破损；当前轮没有重跑 pip check。
这些检查不证明服务已能启动、测试已通过、模型可调用或所有版本兼容。

## 3. 证据索引

### S1 — AgentOpsHub
`backend/src/app/main.py`：现有应用装配 Database、LLMGateway、ToolRegistry、ToolExecutor、AgentRuntime；注册 health/readiness。
`backend/src/app/tools/builtin/__init__.py`：system_status、ticket_search、ticket_create、knowledge_search 四个内置工具。
工单调用本项目 TicketService，不是 SupportOps 远程能力。
`backend/src/app/agents/models.py`：运行上下文、计数、默认 8 turn/16 tool/60 秒等有界参数。
`docs/scenarios/integration-map.md`：跨仓库集成是规划，不是已连通的执行实现。
`pyproject.toml`：Python、LangGraph 与打包边界。

### S2 — 当前本机状态
本轮只读执行 git status/rev-parse、解释器版本、Get-FileHash 与代码文本检查。
未读实际 `.env`、私钥或客户记录；未执行迁移、启动应用、运行全量测试或调用模型。
D:\AGENTS.md 与 D:\Projects\AGENTS.md 未发现；本轮检查前 WorkflowOps 目标目录不存在。
目标目录是否最终写入成功及文件 hash，以文档写入后的验证结果为准。

### S3 — SupportOps 业务与权限
`app/main.py:181`：POST /v1/support/messages。
`app/main.py:277`：POST /v1/actions/{action_id}/confirm。
`app/main.py:306`：GET /v1/operations/{operation_id}。
`app/main.py:330`：POST /v1/agent/operations/{operation_id}/decision。
`app/main.py:351`：GET /v1/orders/{order_id}。
`app/main.py:382` 起：工单列表、分派、状态、消息等运营路径。
`app/domain.py:61–65`：SupportRequest 只接受 conversation_id/message。
`app/domain.py:162–165`：ConfirmationRequest 的 confirm 为 strict bool，禁止额外字段。
`app/store.py:46–58`：OrderRow 含 tenant/customer、全局 order_id 主键；金额使用 Float。
`app/store.py:310` 起：有基础 seed_demo_data，但不是 Spec 中待实现的通用受控 fixture importer。
`app/auth.py:97` 起：JWT claims 中 tenant 必须存在，Principal 包含客户/角色语义。
`app/config.py`：refund_human_review_threshold 默认 500.0，auth_mode 默认 jwt。
`app/operations.py`：客户确认、审批、状态、锁、版本与回执位于领域流程；本地提交不等于支付结算。

### S4 — SupportOps 数据交付
`app/business_data/export_v1.py`：读业务表，按 tenant 导出四个数据集；PostgreSQL REPEATABLE READ + READ ONLY。
`app/business_data/protocol.py`：Business Contract v1 与 Export Format v2 分开；包含确定性 snapshot_id 规则。
`app/business_data/validate_v1.py`：独立目录与记录校验实现。
`scripts/export_business_data_v1.py`：已有 --output、--tenant-id、--currency、--generated-at CLI。
`docs/BUSINESS_DATA_DELIVERY_V1.md`：订单/动作无可靠变更水位，currency 为可信分区元数据，Float 精度不能由导出修复。
未来平台入口不直接暴露这个 CLI 的任意路径/租户参数给浏览器。

### S5 — DataCopilot 受控分析
`backend/app/composition/business_analytics.py`：可信目录、grant、context、consumer、executor 与 SQL pipeline 的组合根。
`backend/app/application/business_analytics/governed_workflow.py`：SQL 计划、执行结果与 scope/snapshot 绑定验证。
`backend/app/infrastructure/business_analytics/delivery_v2.py`：独立交付消费者，封闭 manifest/schema 校验。
`backend/app/infrastructure/business_analytics/duckdb_materializer.py`：对有效 scope 中的 typed rows 建立 DuckDB 表。
`backend/app/presentation/api/routes/business_analytics.py`：认证 API 和 Agent 入口；不是匿名分析服务。
`contracts/external/business_data/v1/acceptance.json`：按 artifact SHA-256 接受 producer contract。
`docs/TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md`：默认无真实生产 IdP/grants，演示不得被描述为生产 SSO。

## 4. 本轮核对的 Contract 字节一致性

文件 A：`D:\Projects\supportops-ai\contracts\business_data\v1\contract.json`
文件 B：`D:\Projects\DataCopilot\contracts\external\business_data\v1\contract.json`

两个实际文件的 SHA-256 均为：
```text
7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b
```

DataCopilot acceptance.json 记录相同 hash。
这只证明 Contract 文件字节和 acceptance 一致，不证明 exporter/consumer 跨进程交付已跑通。
该端到端验证是 P4 的具体工作，不应再次设计一套平行 Contract。

## 5. 当前未证明的事项

没有验证 WorkflowOps 服务能启动，因为融合代码尚未迁移/实现。
没有运行三项目全量测试、24 场景、真实模型评测、真实 producer→consumer 交付。
没有证明生产认证、实时同步、网络分布式执行、支付结算或跨服务持久恢复。
没有下载开源数据或生成模拟数据；当前已选择模拟数据作为默认方案。
本轮新增的是需求/实施文档；后续实现结果必须另有真实命令和证据。
