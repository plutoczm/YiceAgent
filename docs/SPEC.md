# WorkflowOps — 实习面试演示版 Spec

版本：v1.0 | 日期：2026-09-30 | 状态：需求与设计基线；实际实现进度与证据见 PLAN.md
工作目录：`D:\Projects\WorkflowOps`
项目名 WorkflowOps 是本轮采用的工作名称，不要求更改三个原项目的历史名称。

## 1. 产品目标与已确认决策

构建一个 Workflow-first 客服与业务分析工作台：用可复现的模拟数据，串起客户服务、受控业务执行、数据快照交付、运营分析和运行追踪。
面试时应能打开一个入口，演示一个真实闭环，并解释每项技术解决的问题、替代方案和取舍。
“真实闭环”指实际服务调用、实际数据库状态与实际 SQL 计算；不等于真实客户、真实支付或生产级服务。

| 决策 | 用户已确认的边界 |
|---|---|
| 用途 | 实习面试项目，不是公开 SaaS，不追求生产平台的完整功能 |
| Q1 | A：可重复演示、可解释；允许模拟或开源数据 |
| Q2 | A：数据生产主要指生成模拟业务数据，不是自动经营真实交易系统 |
| Q3 | B：一个总目录、最终一个 Git 仓库；允许多服务和多 Conda 环境 |
| 控制逻辑 | 能用确定性 Workflow 就不用自主 Agent；模型只承担必要的语言任务 |

本 Spec 的默认工程选择：采用自生成模拟数据，不把下载外部数据作为首版依赖；保留三个服务进程；新增一个轻量统一 Web 工作台。
这些选择可在不扩大验收范围的前提下调整。任何新增必做功能都必须说明替代掉哪项原工作，不自动追加阶段。

## 2. 完成定义与禁止扩展

首版完成必须同时满足：一个入口；六个验收场景；离线模型模式可走完闭环；运行记录可追踪；原项目有效能力不丢失；技术说明与测试结果可核对。
六个场景是：数据准备、客服查询/有引用回答、退款确认、人工接管、快照分析、失败/越权展示。具体判据见第 12 节。

不纳入 v1 完成条件：
- 公网部署、域名、注册/密码系统、真实 SSO、Keycloak、WSL2 或远程 staging 验收。
- 实时 CDC、Kafka、Airflow、对象存储平台、数据湖、大规模数仓、真实支付退款。
- 多 Agent 讨论、自主分解任务、反思循环、自动修复代码、持久化图恢复或通用任务调度平台。
- MCP/A2A/AG-UI 全协议接入、统一模型 SDK、统一三套 RAG 或强制统一 Python 依赖。
- 新一轮 Dense/Hybrid/Rerank 对比、训练/微调、下载大模型、强制 GPU。
- 高可用、分布式 exactly-once、生产 SLO、全量可观测性后端或合规级审计账本。

已有能力可以保留在服务内部或原有测试中，但不得因此变成演示启动的强制依赖。
不删除原项目，不重写 Git 历史，不执行数据库降级，不自动 commit/push，不覆盖现有秘密配置。

## 3. 三个模块的职责

| 模块 | 来源 | v1 职责 | 不拥有的职责 |
|---|---|---|---|
| Harness | AgentOpsHub | 固定工作流、统一入口、身份映射、调用边界、超时、运行记录、闭环评测 | 不决定退款资格，不直接改订单，不为两个领域再套自主 Agent |
| SupportOps | supportops-ai | 订单查询、退款/退货规则、客户确认、人工审批、工单、业务事实与快照导出 | 不负责分析仓库，不信任模型或浏览器提供的租户权限 |
| DataCopilot | DataCopilot | 模拟数据生成、数据契约校验、受控快照分析、可选 Text2SQL | 不直接修改 SupportOps 业务库，不把生成数据当真实观测 |

界定两种“生产”：DataCopilot 生成的是测试输入；SupportOps 执行业务操作后形成权威业务事实。
模拟数据导入器属于 SupportOps，由其自己的解释器运行；DataCopilot 不导入其 ORM。
AgentOpsHub 原有 Tenant/Ticket 属于参考运行时，不作为融合产品第二套工单真相源。
产品命名可以保留“智能体工作台”，但技术说明必须称其为 Workflow-first 系统，而非三个自治 Agent。

## 4. 目标架构与目录

```text
Browser
   |
   v
Harness: UI + fixed Workflow + typed HTTP adapters + run records
   |                                      |
   v                                      v
SupportOps API                        DataCopilot API
   |                                      |
   v                                      v
PostgreSQL: operational facts        validated in-memory DuckDB
   |
   +-- immutable Delivery v2 -----------> consumer
DataCopilot generator -- synthetic fixture --> SupportOps demo importer
```

跨服务通信采用固定 HTTP API；本机文件快照用于数据交付。先不新增消息队列和通用协议层。
同一 PostgreSQL 实例只承担 SupportOps 演示业务库；Harness v1 运行记录采用本地 JSONL。
DuckDB 是按查询建立的分析快照，不是第二套可写业务库。[R3][R4]

```text
WorkflowOps/
  README.md
  docs/
    SPEC.md
    PLAN.md
    SOURCE_AUDIT.md
    DEMO.md                    # P6 生成
    INTERVIEW.md               # P6 生成
  services/
    harness/                   # AgentOpsHub 源码快照，保留 backend/src/app
    support/                # supportops-ai 源码快照，保留 app
    analytics/               # DataCopilot 源码快照，保留 backend/app
  contracts/
    business_data/v1/           # 唯一发布源；保持原字节与原语义
    demo_fixture/v1/            # 单独的模拟输入协议，不混入业务 Contract
    service_api/v1/             # 新增边界的机器可读 schema/导出 API 契约
  demo/
    scenarios/                 # 六个演示剧本与生成参数
    expected/                  # 冻结的预期规则/统计结果，不是页面假结果
  scripts/
    dev.py                     # 根入口；只调度子进程，不跨服务 import
    import_sources.py          # P1 的安全复制和来源记录
    check_contracts.py
  tests/
    integration/               # 黑盒跨服务测试
  infra/
    compose.demo.yml           # 仅演示所需 PostgreSQL
  .runtime/                    # ignored：本次演示库配置、快照、运行记录、PID
```

此树是目标布局；本轮只创建 README 与三份 docs，不能将规划目录描述为已实现。
最终只保留根目录一个 `.git`；不复制嵌套 `.git`，不引入 submodule。
服务原始布局暂不大规模重命名。文档入口用业务角色解释目录，避免三份 README 互相竞争。

## 5. Workflow 与模型使用边界

LangGraph 官方将固定代码路径的 Workflow 与动态选择流程/工具的 Agent 区分开。[R1]
本项目的设计选择是：主流程没有模型规划节点；用已有 LangGraph 表达一个有条件分支的固定流程，其余简单逻辑使用普通 Python。

| 能力 | 确定性实现 | 允许的模型作用 |
|---|---|---|
| 模拟数据 | 固定 seed、固定版本生成器、schema/引用校验 | 无；不由 LLM 生成业务真值 |
| 任务入口 | 按 UI 动作和固定 workflow_id 选择流程 | 无 |
| 订单/退款/工单 | 服务端校验、状态机、事务、确认/审批 | 不决定授权，不批准，不直接写库 |
| 知识问答 | 现有 BM25 检索、证据门槛、可追溯引用 | 可选：将证据组织为自然语言 |
| 运营报表 | report_id → 版本化 SQL 模板 → AST 授权 → DuckDB | 无 |
| 自然语言分析 | 同一受控执行边界 | 可选：生成一个不可信 SQL 候选 |
| 运行展示 | 真实状态、耗时、错误、关联 ID | 无；不生成“看起来成功”的报告 |

默认 `offline` 模式：关闭模型请求，使用规则路由、证据摘录、固定报表模板；不得把模板结果标成 LLM 输出。
可选 `live` 模式：利用原有模型适配器，输出显式标记来源；默认关闭，不自动读取其他项目密钥或发起计费调用。
一次语言任务最多一次生成，不自我反思、不循环修复、不在失败后重新执行业务写入。
LLM 失败时明确显示失败；可由用户另选模板报表，但不得静默伪装成模型成功。
这不声称所有输出完全确定：业务时间戳/UUID、网络耗时和可选模型文本可以变化；流程顺序、权限与业务不变量由代码控制。

## 6. 数据设计：小而可解释

### 6.1 模拟输入

首版默认：2 个演示租户、每租户 3 个客户标识、每租户 12 个订单，共 24 个订单；固定 `seed=42`。
使用标准库生成，不增加 Faker 或下载依赖。固定生成器版本、seed、参考时间、排序与序列化后，原始 fixture 的 SHA-256 可复现。[R5]
生成输入另用 `demo_fixture/v1`，包含订单和场景参数；不声称 SupportOps 已有客户主数据表。
订单 ID 全局唯一，例如 `ORD-1001` 与 `ORD-2001` 两组，兼容现有业务表主键。
覆盖：可退低金额、高金额需审批、不可退订单、他客户订单、他租户订单、需人工处理的咨询。
演示币种固定为 CNY，并明确这是生成规则，不是从真实订单识别出的币种。

金额默认使用整数元，例如 199、899，避开把源库 Float 伪装成精确资金账本。
Contract 中的 decimal 字符串表示不修复源 Float 精度；迁移真实资金类型不纳入 v1。[S4]
生成器直接产生订单输入；工单、动作和回执通过 SupportOps 既有业务流程形成，不直接伪造“已审批”“已退款”记录。
fixture 可重复生成不等于整个业务数据库字节级复现；运行时 UUID、时间戳与 TTL 使用真实演示时钟，测试比较业务统计和不变量。

### 6.2 导入、复位与隔离

生成器写出 fixture；SupportOps 专用 importer 先完整校验，再写入本项目拥有的演示数据库。
正常导入默认拒绝覆盖非空数据库；重建必须显式 `--reset-demo`，并核验数据库/资源的 WorkflowOps 所有权标记。
禁止操作三个原项目的现有数据库、容器或卷。Web 不提供任意数据库导入/删除功能。
租户 B 可只用于负例测试，不要求做完整租户管理界面。

### 6.3 业务数据交付

沿用 Business Data Contract v1 + Delivery Format v2，包含 orders_v1、tickets_v1、ticket_events_v1、actions_v1。
两个现有 Contract 文件字节哈希已一致；集成端到端行为仍需在 P4 实测。[S4][S5]
导出由 SupportOps 在同一只读快照中完成；消费者独立验证目录、版本、哈希、字段、租户、行数与关联，再执行分析。[S4][S5]
业务 Contract 的三份位置是“根发布源 + 两个受检验快照”，不是三份独立可编辑的真相；脚本/测试保证字节一致。
不借融合扩大 Contract 数据集；导出元数据的版本与业务 Contract 版本分开。

### 6.4 发布与新鲜度

SupportOps 将完整导出写到 `.runtime/deliveries/<trusted-tenant>/<export-id>/`，不得覆盖已发布目录。
export-id 由服务端生成，不能接受客户端路径。发布前 producer validator 必须成功。
SupportOps 随后原子替换受信任的本地发布索引；DataCopilot 只能读取该索引和目录，不能写业务快照。
索引中的路径必须解析到固定根内、拒绝越界与符号链接；DataCopilot 每次查询固定一个不可变目录，避免读到半包或在执行中切版本。
该索引只是“当前本地演示快照”的小映射，不扩展成消息总线、归档平台或调度器。

导出由运营人员显式点击“刷新快照”。客户动作成功不依赖分析刷新；导出失败不得回滚或重放已提交业务。
界面区分业务状态与分析快照状态，并显示 snapshot_id、生成时间、数据来源、可用水位。
订单/动作没有可靠变更水位，因此不宣称实时、增量 CDC 或所有指标截止到同一业务时间。[S4]
哈希用于完整性检查；本机受控目录是演示的来源信任边界，不声称文件已获生产者数字签名。

## 7. 六个演示场景

### WF-01：准备演示数据
CLI 固定生成参数 → fixture 校验 → SupportOps importer → 导入摘要。
输出实际订单数和 fixture_hash；默认重复导入拒绝或明确 no-op，不重复累加。
通过测试/场景驱动调用业务流程准备少量工单，不直接写终态。

### WF-02：客户查询与知识回答
预置客户身份 → 查订单或输入咨询 → 规则路由 → 归属检查/知识检索 → 返回结果。
知识回答在 offline 模式显示命中的原文片段和来源；无充分证据则拒答/转人工。
BM25-only 演示适配器复用现有稀疏检索代码；不把测试用哈希 embedding 包装为真实 Dense 召回。
准确订单查询优先使用显式按钮/字段，不依赖自然语言提取才能成功。

### WF-03：退款确认与高风险阻断
读取订单 → 计算资格 → 创建待确认操作 → 客户明确确认 → 检查审批条件 → 提交本地请求。
默认使用原有 500 元审批阈值；用 199 和 899 两个订单演示低/高金额分支。[S3]
高金额需要独立运营审批，客户不能自己批准。支持取消、过期/漂移拒绝、重复确认不会重复提交。
界面固定用“退款申请已提交”，不得写“退款到账”。

### WF-04：人工接管
无法安全解决 → 创建 SupportOps 工单 → 运营分派 → 真实人工回复 → 客户查看/回复。
沿用原有工单状态机；Harness 不创建第二份业务工单。
人工回复为真实输入，不用模型伪造人工处理记录。

### WF-05：刷新快照与运营分析
运营身份 → SupportOps 导出 → 发布验证 → DataCopilot 校验 → 模板 SQL → AST 授权 → DuckDB → 有界结果。
固定报表：订单总数、退款申请状态分布、待审批数量、工单状态分布、未关闭且超 SLA 工单数。
超 SLA 计算使用服务端明确的 as_of；不把历史固定参考时间当实时当前时间。
不生成缺少字段支持的“退款原因”“实际退款金额到账”“昨日增长率”等结论。
offline 报表仍读实际新快照和执行实际 SQL；禁止将预期文件作为接口响应。

### WF-06：失败与边界
演示一次他客户/他租户访问拒绝；一次坏快照拒绝；一次下游失败的安全错误。
可用预置故障注入测试或演示 fixture，不停止其他项目的服务。
运行面板显示哪步失败、业务是否已提交、是否需要人工核对，不能把未知写入状态说成确定失败。

## 8. API、身份与 Harness 的最小合约

### 8.1 本地身份，不构建账号产品

v1 使用“演示身份切换”：customer-a1、customer-a2、operator-a；tenant-b 仅用于负例。
这是演示器选择预置角色，不是真实登录，不可部署到公网冒充认证。
Harness 在服务端维护角色到租户/客户/权限的固定映射，并用不可猜测的本地 session 句柄保持当前选择。
切换角色会重置会话上下文；客户消息、模型参数、query/body 中的 tenant_id/roles/customer_id 均不能改变授权。

跨服务请求使用每个演示身份、每个目标服务独立的随机 opaque Bearer token；由启动器生成到 ignored 的配置中。
下游 demo-only identity adapter 查验 token 后取服务端固定映射，而非信任裸 X-Roles/X-Tenant 或请求体角色。
token 不下发浏览器、不进入日志/模型；客户端未带 token、错 token、错目标服务 token 必须拒绝。
这不是 OAuth、JWT SSO 或生产认证。已有 JWT/JWKS verifier 与 fail-closed 默认生产入口保持不变。
仅在显式 demo composition 启用 fixture identity provider，并复用既有 principal/context factory 和授权流程。
启动器拒绝非 loopback 服务地址、外部代理和重定向；production 不加载这些 demo 入口。
本机同权限用户能切换演示身份或读取本机配置，不在“租户隔离安全保证”之内；测试证明的是预置上下文下的数据/业务授权。

### 8.2 授权矩阵

| 操作 | customer-a1/a2 | operator-a | tenant-b 测试身份 |
|---|---|---|---|
| 查询订单/确认退款/读取客户工单 | 仅自己且 tenant-a | 不冒充客户自动确认 | 仅自己且 tenant-b |
| 分派/回复工单、审批高金额操作 | 拒绝 | 仅 tenant-a | 不得触达 tenant-a |
| 刷新快照、业务分析、租户内运行详情 | 拒绝 | 仅 tenant-a | 不得触达 tenant-a |
| 生成/重置演示数据库 | Web 不提供 | 仅受保护本机 CLI | 拒绝 |

演示角色切换控制与业务 API 权限是两回事；业务请求不得用 arbitrary persona 参数绕过当前 session。
Harness 运行查询同样按 caller 过滤，不能因拿到 run_id 就读取别人的记录。
拒绝的跨租户对象不得暴露姓名、金额、资源存在性等详情。

### 8.3 现有接口复用

| 来源 | 已有入口 | 复用方式 |
|---|---|---|
| SupportOps | POST /v1/support/messages | 客户消息流程 |
| SupportOps | GET /v1/orders/{order_id} | 明确订单查询 |
| SupportOps | POST /v1/actions/{action_id}/confirm | 显式 confirm 布尔值 |
| SupportOps | GET /v1/operations/{operation_id} | 写入超时后核对已知操作 |
| SupportOps | /v1/agent/tickets 与相关子路由 | 分派、状态、双向消息 |
| SupportOps | POST /v1/agent/operations/{operation_id}/decision | 独立人工审批 |
| DataCopilot | POST /api/v1/business-analytics/query | 可选真实模型分析；原认证边界不削弱 |

现有路径依据 [S3][S5]。内部 Request/Response 继续使用原 DTO；外层只做必要的安全转换。

### 8.4 计划新增接口

以下仅是待实现合约，不表示当前仓库已存在：
- Harness：POST /api/demo/session（仅白名单演示身份）；POST /api/workflows/{workflow_id}/runs；GET /api/runs/{run_id}。
- Harness：客户确认、运营审批和人工回复各自明确的动作入口；禁止开放任意 URL/path 的通用代理。
- SupportOps：POST /internal/demo/exports。输入为空对象；租户、目录与币种由服务端决定。
- DataCopilot：POST /internal/demo/reports。输入只有白名单 report_id；scope 和 as_of 由服务端上下文确定。

workflow_id 只允许 support_lookup、support_message、support_confirm、support_review、ticket_action、refresh_snapshot、analytics_report。
每个 workflow_id 有独立封闭 schema；不接受任意 dict 转发、SQL、执行命令、数据库 URL、文件路径或权限字段。
公开结果封装：run_id、workflow_id、status、steps、domain_refs、typed result/error。
统一状态：completed、waiting_confirmation、waiting_approval、failed、unknown_outcome。
等待人工时本次 HTTP 请求结束，业务状态留在 SupportOps；后续动作启动关联的新 run，不需要 LangGraph checkpointer/resume。
新 schema 必须通过 Pydantic 校验，API 模块按 APIRouter 划分。[R2]

### 8.5 执行、超时与记录

固定节点示例：validate → authorize → invoke_domain → format_result/record。
流程选择由服务端注册表决定，禁止 eval、动态 import、任意插件扫描或模型选择处理器。
v1 配置目标：单次 HTTP 下游调用最多 30 秒，整体同步 run 最多 60 秒；这是设计上限，不是实测 SLO。
Harness 不自动重试写入；下游写入超时标记 unknown_outcome，有 operation_id 则读其状态，没有则要求人工核对，不能重放整段流程。
读取可以手动重试；首版不加通用退避/熔断框架，保留既有模型内部有限重试并记录真实尝试数。
每次 run 写一个独立 JSONL 文件，追加 step 状态和终态；单 worker 演示，异常尾行读取为 incomplete。
记录仅含受控字段：run/parent_run/domain_trace_id、动作类型、状态、耗时、错误类别、模型模式、可用 usage/cost。
不记录凭据、完整对话、SQL、行数据、工单正文；必要身份归属存受控本地元数据用于授权，不对其他身份输出。
跨服务的域 trace_id 映射到 run_id，不强制改写领域原有 request_id。没有生产持久恢复/篡改防护承诺。

## 9. 技术栈与为什么使用

| 技术 | v1 用途 | 不选更复杂方案的理由与局限 |
|---|---|---|
| Python + FastAPI + Pydantic | 复用三个后端；明确 API 和输入/输出验证 | 不再引入另一套后端语言；类型校验不等于授权 [R2] |
| LangGraph（Harness 现有版本） | 一个固定有条件分支的工作流，可视化与测试节点顺序 | 简单规则仍写 Python；不启用自主规划/checkpoint/多 Agent [R1] |
| PostgreSQL + SQLAlchemy + Alembic | 业务状态、约束与事务；保留退款并发语义 | 不用缓存代替业务真相；只读一致快照用于导出 [R3] |
| Contract JSON + JSONL + SHA-256 | 看得见的 producer/consumer 边界、完整性和版本约束 | 小规模本地快照足够；不为面试演示加 Kafka/CDC [S4] |
| DuckDB + SQLGlot（DataCopilot 原有） | 授权后对小型分析快照执行 SQL | 不搭分布式数仓；SQL AST、外部访问禁用和资源限制仍需保留 [R4][S5] |
| BM25 | 小型政策库的可解释词法基线 | 默认不启动向量库/embedding；词义改写能力有限 |
| HTTP + 固定适配器 | 三进程隔离现有依赖，明确权限边界 | 有网络错误与更多进程成本；此处不以“大规模微服务”包装 |
| HTML/CSS/JavaScript | 复用客服页面资产，提供单一静态工作台 | 不新增 React/Vue 构建链或第二个必开前端 |
| Conda + Docker Compose | 现有 Python 环境隔离；一个可清理的演示 PostgreSQL | Conda 不复制进仓库；完整容器化不是 v1 必需项 |

默认不启动 Redis、Qdrant、Chroma、Ollama 或 BGE；原有可选功能保留但不得触发启动时下载/探测。
不升级三套框架到同一版本。保留各服务依赖文件及已验证约束，新增依赖最小化。
技术选型说明不是宣称单一方案普遍更优；选它们是为了复用当前代码并服务本项目的可验证目标。

## 10. 环境与启动合约

| 服务 | 现有环境 | Python | 计划端口 |
|---|---|---|---|
| Harness + UI | agentopshub | 3.12.14 | 127.0.0.1:8010 |
| SupportOps | supportops-ai | 3.11.15 | 127.0.0.1:8011 |
| DataCopilot | datacopilot | 3.11.15 | 127.0.0.1:8012 |
| 演示 PostgreSQL | Docker 独立项目 | 镜像版本在 P1 固定 | 127.0.0.1:55432 |

端口是拟定配置；若被占用，明确报错，绝不 kill 占用者或接用未知服务。
根 scripts/dev.py 用标准库 subprocess 分派，每个服务固定自己的 cwd、解释器和 PYTHONPATH。
必须校验导入来源在新目录内，尤其两个顶层 app 包，防止误用原仓库 editable install。
可以通过子进程 PYTHONPATH 正确绑定源码，不要求重绑或升级现有 Conda 环境。
不把三个服务同时加到同一 PYTHONPATH；不跨服务 import；不执行根级裸 uv run。

未来 CLI 合约（均待 Plan 实现）：
`doctor`、`demo-up`、`seed-demo`、`demo-check`、`demo-down`、`check-contracts`、`test-service <name>`、`test-integration`、`verify --offline`。
`seed-demo --reset-demo` 是唯一显式演示重置入口，需校验所有权；demo-down 默认保留数据。
doctor 只检查不修改；demo-up 不安装依赖、不下载模型、不自动迁移原库；对项目自有空演示库执行明确迁移。
进程停止按 PID + 可执行文件 + 启动标记验证所有权，不按端口批量杀进程。
全程一套默认 demo 配置，不要求用户复制三套真实 .env。秘密只存 ignored 的本地配置。
首次 Docker 镜像准备可能需要网络，必须明确显示；offline 指无需模型/数据外部请求，不代表无需本机数据库运行。

## 11. 源码迁移策略

当前三仓库状态详见 SOURCE_AUDIT.md；SupportOps 存在未提交和未跟踪的有效数据交付代码。
P1 复制“审阅后的工作树文件”，不是只 clone 远端或导出 HEAD。已跟踪修改与允许的未跟踪源码/测试均纳入来源清单。
源码、依赖、迁移、必要内置政策/测试 fixture 与 LICENSE 保留；不复制 .env、.git、环境、缓存、模型权重、数据库文件、运行日志、egg-info。
不盲目复制整个 data/models/.tools；对必要资源建立 allowlist，遇符号链接/重解析点不跟随。
每个目标文件记录原仓库、原相对路径、HEAD、是否未跟踪、内容 SHA-256；冲突停止并给出差异，不覆盖。
根 Contract 采用当前一致的字节版本；历史文档明确归档/标记原项目语境，避免旧计划继续驱动新主线。
三个原目录完整保留。新根目录的 Git 初始化、提交与推送由用户明确授权时执行；文档生成本身不执行。
历史提交不必合并到新仓库；来源 manifest、旧仓库和 LICENSE 保留可追溯性。

## 12. 验收标准

所有 AC 均是未来必须实测的目标，不是已取得的结果。
| AC | 可观察验收条件 |
|---|---|
| AC-01 | 新目录中启动与运行；三个服务 import 来源全在新根目录内；导入/整合时原目录与原数据不变。2026-10-02 用户授权原目录/私有备份退役后，当前来源按冻结 manifest 哈希核对，不再依赖原目录，见 REPOSITORY_RETIREMENT.md |
| AC-02 | 同 seed/version/reference-time 生成 fixture 字节一致；24 个订单、两租户、引用合法；重复导入不重复累加 |
| AC-03 | 浏览器一个入口完成客户与运营两种视图；后台三服务均有真实请求，不是三个网页链接或硬编码回复 |
| AC-04 | 自己订单可读；他客户/他租户订单拒绝；客户不能读运营报表或他人 run |
| AC-05 | 低金额先确认再提交；重复确认不重复提交；高金额未批准不提交；页面不声称支付完成 |
| AC-06 | 一条真实工单实现分派、人工回复与客户继续回复；所有记录只在 SupportOps |
| AC-07 | 从真实演示业务库导出，DataCopilot 真正校验并执行；刷新前后至少一个状态统计随业务变化 |
| AC-08 | 固定模板报表与独立计算的期望一致；坏 hash/tenant/schema/越界 SQL 被拒绝；不使用 expected 作为响应 |
| AC-09 | run 记录包含实际步骤与终态；敏感内容不落日志；下游错误可定位；写入未知不自动重放 |
| AC-10 | offline 无模型/embedding 请求与下载；关闭向量库/Redis 后主闭环仍可运行 |
| AC-11 | 24 个跨模块用例通过，必要既有回归不弱化；测试报告保留失败/skip，不把 skip 当验证 |
| AC-12 | DEMO/INTERVIEW 能对应真实代码和实测结果；未实测在线模型与生产能力清楚标识 |

P6 完成以上判据即冻结 `v1-demo-ready` 状态。不自动开启下一轮基础设施、协议或检索优化。

## 13. 证据索引与公开技术依据

本地代码证据由 SOURCE_AUDIT.md 列出路径和快照。其内容表示“检查到的实现”，不表示已完成跨项目运行测试。
[S1] AgentOpsHub main.py、tools/builtin/__init__.py、agents/models.py、pyproject.toml。
[S2] 三项目 Git 工作树与各 Conda 解释器检查，2026-09-30。
[S3] SupportOps app/main.py、domain.py、operations.py、store.py、auth.py、config.py。
[S4] SupportOps app/business_data/*、docs/BUSINESS_DATA_DELIVERY_V1.md、业务 Contract。
[S5] DataCopilot governed_workflow、composition/business_analytics、delivery_v2、duckdb_materializer、认证路由与 acceptance.json。

以下公开文档于 2026-09-30 查阅；只用来解释机制，不用于证明本机功能已运行：
[R1] LangGraph Workflows and agents：https://docs.langchain.com/oss/python/langgraph/workflows-agents
[R2] FastAPI Bigger Applications：https://fastapi.tiangolo.com/tutorial/bigger-applications/
[R3] PostgreSQL Transaction Isolation：https://www.postgresql.org/docs/current/transaction-iso.html
[R4] DuckDB Security：https://duckdb.org/security
[R5] Python random（复现需固定生成器代码/运行时，不泛化跨版本一致性）：https://docs.python.org/3/library/random.html
