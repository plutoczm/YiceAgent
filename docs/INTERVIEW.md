# WorkflowOps 面试说明

## 业务问题与闭环

客服系统的难点是让查询、确认、人工审批和分析在同一条可解释流程上工作，同时保留权威业务边界。WorkflowOps 用固定 seed 的模拟订单展示：客户查询与证据回答 → 退款确认或人工接管 → SupportOps 持久业务事实 → 不可变交付 → DataCopilot 受控 SQL → Harness 运行追踪。

这是实习面试演示项目。真实指实际 HTTP、PostgreSQL 状态和 DuckDB 计算；数据是模拟的，支付、真实用户登录与生产部署没有实现。详细验收结果以 [P6_VALIDATION.md](P6_VALIDATION.md) 为准，不将历史测试数当作生产指标。

## 架构与取舍

**为什么不做大 Agent？** 退款资格、授权、审批和持久化状态需要明确不变量。主流程按七种 workflow_id 运行，由 Pydantic 封闭输入、确定性权限检查和固定 LangGraph 路径决定步骤，不让模型规划写操作。模型只适合语言任务；当前 offline 使用 BM25 摘录和 SQL 模板。原 Agent/tool runtime 通过独立回归保留，不成为工作台自主 supervisor。

**为什么服务不直连业务库？** SupportOps 是唯一操作业务事实的所有者。Harness 通过 typed HTTP adapter 调用领域接口；DataCopilot 只消费受检验交付，不持有可写业务副本。这样审批、确认、幂等和租户边界不会被另一个服务绕过。跨服务 import 被环境分派隔离，三个 Conda 环境没有合并。

**为什么保留三进程，而不直接改成单体？** 原项目已有不同依赖环境和可复用的领域实现，HTTP 边界能以较小改动组合这些能力并独立验证授权。代价是要处理进程启动、超时与跨服务失败；对全新小项目，单体也合理。本项目没有为了展示微服务而继续拆分，也不为减少进程而合并现有环境或重写业务规则。

**为什么 PostgreSQL + DuckDB？** PostgreSQL 保存订单、待确认动作、审批、工单和回执，负责事务与并发不变量；导出使用真实 repeatable-read/read-only 快照。DuckDB 按每个查询建立分析视图，执行经过 AST 授权的 SQL。它不会写回业务库，也不是另一个业务真相源。

**为什么保留 Contract？** Business Data Contract v1 和 Delivery v2 让 producer 与 consumer 独立检查版本、字段、bytes/hash、租户、引用与行数。发布先完整验证，再原子更新本地索引；每次查询固定不可变目录。哈希证明完整性，本机受控目录承担来源信任，不宣称生产者数字签名。

**为什么暂不用向量库、MCP 或 Kafka？** 当前小数据闭环已有 BM25、固定 HTTP 和本地不可变文件交付，足以验证目标。新增协议、队列或 Dense 会引入部署和调优工作，当前验收没有证明需要。保留原代码能力与给演示增加必需依赖是不同决定；v1 完成后不追加架构阶段。

## 失败治理与安全

客户身份来自 Harness 固定服务端映射；opaque token 按目标服务分开，浏览器拿不到下游 token。角色切换只是演示，不能冒充生产认证。query/body/header 中的 tenant/customer/role 字段不能提高权限。run_id 也受调用者 ACL 保护。

低金额要客户明确确认；高金额要独立运营审批和客户确认。重复确认不重复提交，取消/过期/漂移不会恢复旧授权。LLM 候选即使存在也必须过确定性检查，不能授权、批准或执行任意 SQL。

Harness 区分派发前失败、只读失败和写入未知；写请求开始后超时或断连保守标为 unknown_outcome，使用关联 operation_id 核对，不自动重放。业务提交与快照刷新独立；刷新失败不回滚已提交业务。业务结果已确认而运行记录失败时，UI 保留真实回执并独立告警。

日志仅保留步骤、耗时、错误类别、状态和关联 ID，排除消息正文、token、SQL 和结果行。JSONL 不是恢复引擎，没有图 checkpoint/resume 或合规审计账本承诺。

## 可核对的工程证据与项目介绍

入口为 scripts/dev.py；服务分派只启动对应环境的子进程。固定流程见 services/harness/backend/src/app/workflows；领域授权与业务事务见 services/support/app/operations.py、workflows.py 和 store.py；分析模板及消费见 services/analytics/backend/app/application/demo_reports 与 infrastructure/demo_delivery。

P6 的 demo/scenarios/e2e-v1.json 定义 E01–E24 和 AC 映射。tests/integration/test_demo_v1.py 在独立 PostgreSQL schema 执行真实 HTTP，用独立 Python 业务行 oracle 验证五种实际 SQL 结果，并显式复用取消/过期/漂移、SQL 和 transport 的确定性回归。JUnit 分列 pass/fail/skip；原 runtime/tool/typed LLM 回归验证的是确定性契约，不是真实模型质量。P5 的六剧本浏览器实测是独立历史证据。

可用的项目介绍结构：**问题**（客服操作与分析需要安全闭环）→ **职责**（Harness 编排、SupportOps 事实、DataCopilot 分析）→ **实现**（封闭输入、状态机、Contract、AST 门禁）→ **验证**（隔离 PG、真实 HTTP、独立 oracle、故障注入）→ **边界**（模拟数据、offline、未做真实支付/生产认证）。只将自己实际实现和验证的职责写进简历；测试数字引用 P6_VALIDATION 的执行结果，不能改写成 QPS、准确率、退款到账率或生产可用性。

## 诚实说明

BM25 依赖词面，不能声称解决语义改写召回。源 Float 金额仍有限制，Contract decimal 字符串没有改变源存储。快照显式刷新、不实时、没有统一 CDC 水位。模拟身份不等于登录，单 worker run log 不等于恢复机制，领域幂等不等于跨服务 exactly-once。

真实模型调用、模型/部署配置验证、成功失败样本、usage 和费用均为 **NOT_RUN**。离线闭环没有模型调用，不提供真实模型质量指标。公网部署、生产 SSO、高并发、真实支付、原项目数据库内容审计与全量可选模块回归不在验收范围内。原仓库来源文件哈希/Git 状态与本项目主演示数据库前后指纹由验证入口检查。

Post-P6 仓库整合：物理服务目录已整理为 `services/harness`、`services/support`、`services/analytics`；原 CLI 标识和 Conda 环境保持兼容。当前运行与历史路径说明见 [Monorepo 指南](MONOREPO.md)；本次仅仓库整合，P1–P6 开发状态不变。
