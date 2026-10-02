# WorkflowOps — 可执行 Plan

版本：v1.0 | 日期：2026-09-30 | 依据：[SPEC.md](SPEC.md)
范围：六个执行批次 P1–P6；每批只交付一个可验证增量；不沿用原项目的无限 Phase 扩展。
当前状态：P1–P4 已于 2026-10-01 验证完成，P5–P6 已于 2026-10-02 验证完成。P6 24 场景与 AC-01–AC-12 门禁通过，冻结 v1-demo-ready，停止主线。实际证据见 P1–P5_VALIDATION 及 [P6_VALIDATION.md](P6_VALIDATION.md)。

## 1. 执行约束与进度板

工作根目录：`D:\Projects\WorkflowOps`。原始输入为三个原项目的本机工作树，而不是仅 GitHub HEAD。
执行者先读取 SPEC、SOURCE_AUDIT 和本 Plan；用户新指令优先于本文默认工程选择。
原目录、原数据库、原密钥保持不变；禁止 reset/clean/stash、强推、删除数据卷或盲目覆盖。
默认不调用模型、不联网下载模型/数据、不升级原有依赖；没有请求就不 commit/push。
某项旧的非核心能力阻塞时，保留代码与测试，标记为非默认 profile，不把其扩展为主线。
不得降低安全断言、替换为 mock 全链路、把 expected 数据硬编码到页面或把 skip 写成通过。

| 批次 | 交付物 | 前置 | 状态 | 完成判据 |
|---|---|---|---|---|
| P1 | 安全单仓库布局与环境分派入口 | 文档基线 | COMPLETE | 698 文件哈希一致；三环境来源/端口/Docker/Contract 通过；原目录不变；根 Git 已独立初始化 |
| P2 | 可复现模拟输入与 SupportOps 受控导入 | P1 | COMPLETE | fixture 稳定；真实 PostgreSQL 24 订单、no-op/reset/回滚/边界通过；见 P2_VALIDATION |
| P3 | 固定 Workflow、demo 身份、客服 HTTP 适配与运行记录 | P2 | COMPLETE | Harness 26、安全 153、真实 HTTP 1、根边界 23 passed；见 P3_VALIDATION |
| P4 | 生产端真实导出→消费端校验→模板 SQL 分析 | P3 | COMPLETE | governed 279 passed/1 skipped；producer 36 passed/1 skipped；真实 delivery 6、reports 1 passed；五报表与独立 oracle 一致，见 P4_VALIDATION |
| P5 | 一个浏览器工作台 | P4 | COMPLETE | UI 5+真实 HTTP 1；浏览器六剧本完成；启动/停止/权限/新鲜度通过，见 P5_VALIDATION |
| P6 | 24 场景验证、演示手册、技术取舍与面试说明 | P5 | COMPLETE | 24 passed，AC-01–AC-12 有证据；必要回归与离线 D5 50 场景通过，2 平台 skip 单列；v1-demo-ready，见 P6_VALIDATION |

每批汇报只包含：改动文件、实际命令/结果、未验证项、剩余批次。失败则修复当前批次或如实保留阻塞，不另建一串子阶段。
P6 后只修复阻塞演示的缺陷；新协议、部署、检索优化进入非必做清单，不能延长 v1 完成线。

## 2. P1 — 安全导入与运行边界

### 目标与改动范围
建立一个最终可成为单 Git 仓库的目录，不做同进程合并，不先改业务代码。
新增 `scripts/import_sources.py`、`scripts/dev.py`、`.gitignore`、`infra/compose.demo.yml`、三个服务目录和来源清单。
保留已有 README、docs 基线，不覆盖本次生成的 SPEC/PLAN/SOURCE_AUDIT。
服务映射：AgentOpsHub→services/harness；supportops-ai→services/supportops；DataCopilot→services/datacopilot。

### 操作顺序
1. 刷新三个源仓库的 HEAD、branch、porcelain 状态；记录允许复制文件的 SHA-256，并检查目标是否已有同名文件。
2. 按 allowlist 收集已跟踪源码及修改、有效未跟踪源码/测试/文档。特别纳入 SupportOps app/business_data、contracts、scripts 和新增测试。
3. 不复制 `.git`、`.env`、私钥、环境、缓存、模型、数据库、运行 artifacts 或 egg-info；不跟随符号链接/重解析点。
4. 先 `--dry-run` 输出将复制/排除/冲突的路径清单；脚本 `--apply` 只向新目录创建文件，已有非同内容文件报错。
5. 保存 `docs/source-manifest.json`，含 source repo、HEAD、原/新相对路径、tracked 状态、SHA-256。原 LICENSE 原样保留。
6. 根 Contract 从 producer 原字节复制；服务两份快照暂保留相对路径，新增检查脚本要求三者相同，不能先改哈希绕过 consumer 验证。
7. 根 dev.py 仅做子进程分派；harness PYTHONPATH=其 backend/src，supportops=其服务根，datacopilot=其服务根，分别设置 cwd。
8. `doctor` 检查 Conda 解释器、包导入来源、所需模块、端口、Docker 可用性；只检查不安装、不改配置、不启动模型。
9. 配置唯一 Compose project `workflowops-demo`，固定兼容 PostgreSQL 镜像主版本/具体标签，记录所选版本；禁止复用未知 DB/卷。
10. 后续执行 P1 时可初始化新根 Git；只一个 `.git`，不合并历史、不自动提交。若未执行 git init，应明确单仓库初始化尚待完成。

### 已有代码的针对性检查
检查各项目相对路径、静态资源、Contract 路径和测试 cwd，避免搬迁后隐式读取旧目录。
两个 `app` 包不能在一个解释器中同时导入；不构造全局 PYTHONPATH，不把三个 requirements 合并。
现有 editable install 可能指向原目录。优先以子进程 PYTHONPATH 覆盖并校验 `__file__`，不直接重绑原环境。
保留原服务历史测试入口；根 `test-service` 分别调用各自 pytest，不在根 pytest 收集三套源码。
对 DataCopilot 的重模型/记忆组件计划引入 demo-only lazy composition；P1 不加载或下载它们。

### 验证命令（P1 实现脚本后执行）
```powershell
Set-Location D:\Projects\WorkflowOps
conda run -n agentopshub python scripts/import_sources.py --dry-run
conda run -n agentopshub python scripts/import_sources.py --apply
conda run -n agentopshub python scripts/dev.py doctor
conda run -n agentopshub python scripts/dev.py check-contracts
```

### 验收、证据与回退
AC-01 部分、AC-10 环境部分；报告三个 `sys.executable`、各包实际 `__file__`、源/目标哈希一致性、排除文件与端口检查。
源仓库 HEAD/status 应与操作前一致；目标无秘密/数据文件，无嵌套 Git。
回退只清理本批明确创建且可核对内容哈希的目标文件；不回退用户已有文档，不碰原目录/环境。
产物：`source-manifest.json`、`environment-audit.json`（秘密脱敏），放 docs 或 ignored runtime 的合适位置。

### P1 实测状态（2026-10-01）

规定四命令均 exit 0；新增边界单测 8 passed；选定搬迁回归 Harness 101 passed、SupportOps 31 passed/1 skipped、DataCopilot 37 passed。Skip 为 Windows 无法创建符号链接，不计为通过。
698 个源/目标文件 SHA-256 全一致，三源 HEAD/branch/status 不变；根/producer/consumer Contract 与 acceptance 一致；三解释器及实际导入路径在新服务目录内，四端口空闲，Docker/Compose/daemon 可用。
lint、format 与 Compose config 校验通过。详见 [P1_VALIDATION.md](P1_VALIDATION.md)、source-manifest.json；environment-audit/import-audit 与完整命令日志存 ignored `.runtime/`。
P1 Definition of Done 已满足（仅 AC-01/AC-10 对应部分）。根 `.git` 已于 2026-10-01 按用户明确授权独立初始化，Git 根为 D:/Projects/WorkflowOps；本次授权仅提交 P1 基线，不 push。P2–P6 保持 NOT_STARTED。

## 3. P2 — 模拟数据与领域导入

### 目标与改动范围
新增 `contracts/demo_fixture/v1/schema.json`、`demo/scenarios/default.json`、DataCopilot `scripts/generate_demo_data.py`。
SupportOps 新增 `app/demo/fixtures.py` 与 `scripts/import_demo_fixture.py`；新增对应单测。
root dev.py 新增 `seed-demo`；生成与导入分别在 datacopilot/supportops-ai 环境运行。

### 操作顺序
1. 冻结 fixture v1：seed=42、2 租户、6 客户标识、24 订单，金额为整数元，订单主键全局唯一。
2. 定义低金额、高金额、不可退、他客户、他租户等明确业务样本；版本化原始输入规则，不让随机数破坏所需场景。
3. 使用局部 `random.Random(seed)`，不依赖随机 hash/集合遍历顺序；固定输入参考时间、排序和 JSON 序列化。
4. 生成器输出 `fixture.json` 和 `fixture-manifest.json`；不直接生成正式 Delivery，避免绕过业务事实源。
5. SupportOps importer 验证 schema、主键、客户/租户归属、金额、输入哈希；全部验证通过后单事务导入订单。
6. 创建本项目拥有的演示 PostgreSQL 数据库；迁移使用服务自己的 Alembic。禁止调用原项目库、自动回填旧库或复用 supportops.db。
7. 默认拒绝非空目标；显式 --reset-demo 需匹配 WorkflowOps DB/容器所有权标记。不接受任意 DSN 或随意选择被重置数据库的 CLI 输入。
8. 工单/动作样本由场景驱动调用领域 workflow 创建；允许业务层产生运行时间/UUID，不手工插入退款终态或审批回执。
9. 记录导入批次和 fixture hash（可为本地受控标记，不新增通用数据平台）；相同输入不会静默重复累加。
10. 分离“原始 fixture 字节可复现”与“业务运行结果可复现”；golden 比较状态/计数/金额，时间字段按显式规则处理。

### 验证命令（P2 实现后执行）
```powershell
conda run -n agentopshub python scripts/dev.py seed-demo --seed 42
conda run -n agentopshub python scripts/dev.py seed-demo --seed 42
conda run -n agentopshub python scripts/dev.py test-service supportops --group demo-data
conda run -n agentopshub python scripts/dev.py test-service datacopilot --group demo-data
```
第二次导入应明确拒绝或 no-op，不能生成第二批订单。
在测试拥有的目标上测试 `seed-demo --reset-demo`；不要为验证防护而尝试改写用户真实数据库。

### 验收、证据与回退
覆盖 AC-02；产物为两次生成 hash、导入行数、负例结果。fixture 文件不是伪造的业务查询结果。
拒绝无归属/坏金额/重复主键输入；无损坏的部分导入；越界 reset 请求产生零写入。
回退只销毁本次创建的测试资源或演示资源，并先校验所有权；默认停止服务不删除卷。

### P2 实际状态（2026-10-01）

规定四条命令及显式 reset 均 exit 0；SupportOps P2 21 passed、DataCopilot P2 3 passed，根测试最新 13 passed；相关 SupportOps 回归 169 passed/1 skipped（Windows symlink，不计为通过）。Ruff lint/format、Contract 和 verify-import 通过。两次 seed 返回相同 hash/no-op，订单保持 24 个；真实 PostgreSQL 并发、事务回滚、归属与查询隔离均有测试。审阅后显式固定 Compose 项目名，新增环境覆盖回归及实际 seed 验证通过，并修复 README 命令围栏。详细文件、命令、资源及限制见 [P2_VALIDATION.md](P2_VALIDATION.md)。P2 完成后停止，P3–P6 未执行。

## 4. P3 — Harness 固定工作流与客服闭环

### 目标与改动范围
Harness 新增 `backend/src/app/workflows/`、`adapters/supportops.py`、`demo/identity.py`、`api/workflows.py`、`runs/` 和 demo composition。
SupportOps 新增 demo-only composition/identity adapter；保留现有 JWT 入口与所有领域规则。
root dev.py 新增 demo-up/down、隔离测试入口。仍不做 UI 与自主 Agent。

### 操作顺序
1. 从 SPEC 第 8 节定义封闭的 run/action DTO、workflow 白名单、错误枚举；注册固定 workflow，未知类型拒绝。
2. Harness demo factory 不实例化不需要的 PG ticket/knowledge、向量库或 LLM；已有内部 AgentRuntime 保留但不驱动主闭环。
3. 用现有 LangGraph 版本实现 validate→authorize→invoke_domain→format/record；分支由显式动作与领域返回决定，无规划循环。
4. 启动器生成独立的服务目标/演示身份 opaque token 映射，仅写 ignored 配置；Harness session 映射只在服务端构建。
5. SupportOps demo identity adapter 验 token 后构建原 Principal。不可相信浏览器传入裸角色/租户，不修改 production 默认认证行为。
6. HTTP adapter 使用固定 base URL 与路由白名单、封闭 DTO、禁止重定向/外部代理；复用现有 HTTP 客户端依赖，不构建通用 URL 代理。
7. 接通订单查询、消息、确认、operation 查询、审批、工单分派/回复/状态。以明确动作入口补充自然语言，不依赖模型识别才能演示。
8. 维持客户确认与管理员审批两条独立权限路径；禁止 Harness 自动批准，禁止写入超时后重放。
9. 建立 run 元数据/每 run JSONL：归属、步骤、耗时、domain_refs、终态；过滤凭据、正文、SQL、结果行。
10. 返回 waiting_confirmation/waiting_approval 时结束本次 run；下一人工动作成为关联新 run。运行记录不承担领域幂等或分布式恢复。
11. 子进程始终固定 cwd/PYTHONPATH，启动检查只做安全 health；退出只停止本项目拥有的进程。
12. offline 支持 BM25-only 证据摘录模式；哈希 embedding 的测试分支不得标成真实语义检索。

### 验证命令（P3 实现后执行）
```powershell
conda run -n agentopshub python scripts/dev.py demo-up --mode offline
conda run -n agentopshub python scripts/dev.py test-service harness --group workflow
conda run -n agentopshub python scripts/dev.py test-service supportops --group safety
conda run -n agentopshub python scripts/dev.py test-integration --group support
```

### 验收、证据与回退
覆盖 AC-04、AC-05、AC-06、AC-09 的 API 部分。
至少一次低金额确认、一次高金额阻断、一次人工往返使用真实 HTTP 和演示数据库，不全程 MockTransport。
错误 token/客户冒充运营/他租户对象/他人 run 均失败；验证拒绝路径无业务副作用。
写入超时标记 unknown_outcome，保留可查询 operation_id；日志无 token、对话、SQL 或结果行。
回退：停新 demo 入口，原项目仍原样可用；保留已发生的演示操作，不声称软件回退会撤销数据库提交。

### P3 实际状态（2026-10-01）

规定四条命令与 demo-down 均 exit 0。Harness 26 passed、SupportOps safety 153 passed、真实 HTTP + PostgreSQL 组合闭环 1 passed、根边界 23 passed，均无 skip；Ruff lint/format、13 个源码的 strict mypy、Business Contract、698 文件原项目保护及 diff 检查通过。集成测试复用真实 fixture importer，在独立 PostgreSQL schema 中运行 26 次 workflow 后清理；低金额确认/重复确认、高金额独立审批后客户确认、人工工单往返、越权拒绝与运行隐私均有证据。写超时和 60 秒截止使用故障注入验证 unknown_outcome、不重放和已知 operation_id。详细命令、文件与限制见 [P3_VALIDATION.md](P3_VALIDATION.md)。

最终已停止两个 demo HTTP 进程；PostgreSQL/数据卷保留。早期在主演示库实际确认的 ORD-1001 请求保留，未 reset；后续测试 schema 已清理。未启动 DataCopilot API、未实现 P4、UI 或 live 模型。验证阶段未 commit/push；复审通过后按用户授权提交 P3，不 push。P3 到此停止，P4–P6 保持 NOT_STARTED。

P3 审阅三项修复已于 2026-10-01 验证：确认无 parent 也记录 operation_id；只有已派发且结果未确定的写请求标记 unknown_outcome；测试 schema 准备失败也执行归属核验清理。新增/扩展回归已纳入上述最新结果，原日志与业务提交保留。详见 P3_VALIDATION 的审阅修复节及 ignored `.runtime/p3-review-fixes/`。

## 5. P4 — 快照交付与确定性报表

### 目标与改动范围
SupportOps 新增受保护 `internal/demo/exports` 路由与演示目录发布适配；复用 exporter/validator。
DataCopilot 新增 `application/demo_reports/`、`infrastructure/demo_delivery/`、demo identity/composition、`internal/demo/reports`。
新增跨服务 Contract 与真实交付测试；不让 DataCopilot import SupportOps，不改 Business Contract v1 语义。

### 操作顺序
1. 固定输入/输出：exports 输入空对象；reports 输入只有 report_id。tenant/path/policy/as_of 来自可信配置与身份。
2. SupportOps 导出至新目录，producer 校验后发布受控索引；失败不更换有效索引。禁止覆盖已发布包。
3. 发布索引写者只有 SupportOps；读取者 DataCopilot 校验根目录/租户映射/越界/链接。每查询锁定一个不可变目录。
4. 使用 DataCopilot 独立 consumer 再验 bytes/hash/schema/tenant/references，不因 producer 已验证而跳过。
5. 为 DataCopilot demo composition 注入预置身份映射，复用已有 grant/context factory；保留生产 JWT/JWKS 默认 fail-closed。
6. 构造 ManagedSQLGeneratorPort 的模板实现：report_id→版本化候选 SQL；复用 AST 授权、隔离 DuckDB、结果限制与安全审计。
7. 不把 report_id 塞成自由文本让模型猜。新增 report service 解析固定 ID，再构造既有 pipeline 所需请求与可信上下文。
8. 固定五个报表：orders_total、refund_requests_by_status、pending_approval_count、tickets_by_status、overdue_open_tickets。
9. SLA 使用明确服务器 as_of；模板/测试利用实际 Contract 字段，不推断未记录的退款原因、付款结算或每日趋势。
10. Harness 接通 refresh_snapshot/analytics_report；刷新是显式运营动作。业务提交成功与分析刷新失败分开呈现。
11. UI/API 输出提供 actual data/row_count、snapshot_id、生成时间、as_of（适用时）、template/live 来源；不返回本机路径或原始 SQL。
12. 可保留原有 live Text2SQL 入口并添加模式标识；不调用模型来验收 P4，不把真实模型成功作为本批前置。

### 验证命令（P4 实现后执行）
```powershell
conda run -n agentopshub python scripts/dev.py check-contracts
conda run -n agentopshub python scripts/dev.py test-service datacopilot --group governed
conda run -n agentopshub python scripts/dev.py test-integration --group delivery
conda run -n agentopshub python scripts/dev.py test-integration --group reports
```

### 验收、证据与回退
覆盖 AC-07、AC-08：一次退款/工单操作，刷新前后比较真实查询统计；保存 snapshot fingerprint 与 run 关联。
独立 oracle 直接由测试明确的业务输入计算预期，不使用被测 SQL 自己当标准。
坏 hash、错租户、坏 schema、越界路径、非法 SQL、超结果限制均拒绝；正常旧快照仍可查询。
回退仅切回经过验证的已有快照索引；不删业务事实，不将新业务数据悄悄说成已回滚。
大数据吞吐、实时增量、索引归档、消费者签名与分布式总线不在本批增加。

### P4 实际状态（2026-10-01）

四条规定命令全部 exit 0。DataCopilot governed 279 passed/1 skipped；SupportOps producer/Contract/真实 PG 快照 36 passed/1 skipped；真实 HTTP delivery 6 passed、reports 1 passed（19 个 workflow run、五个独立 PG 行数据 oracle）。额外 Harness 32、SupportOps safety 153、P3 HTTP 1、根边界 23 passed。两项 skip 是原有 POSIX 专用/Windows 无 symlink 权限测试，不计为通过；新增 Windows junction 拒绝与真实 PG repeatable-read/read-only 测试通过。

SupportOps 单独发布不可变 Delivery v2 目录和原子 current 索引；消费者固定一个目录，独立校验再走原 AST/DuckDB 管线。低金额退款提交与高金额待审批后，报表由空分布变为 pending_approval=1、submitted=1；旧快照仍可实际查询。五报表返回真实 rows/row_count、快照/水位、生成时间、as_of、耗时与 template v1 来源。坏 hash/tenant/schema/path/fingerprint、非法 SQL、越权和错服务凭据被拒绝；刷新失败保留已提交业务与旧索引。

29 个 Python 文件 Ruff lint/format、配置的 Harness strict mypy（6 个本次源码）、Business Contract、698 文件与原仓库保护、diff 检查通过。无模型调用/安装/commit/push；未改 Business Contract、原项目源码、原数据库或用户级配置。真实集成使用独立自有测试 schema，结束后遗留 0。主演示库仍为 24 订单、0 工单、1 pending action、1 receipt，P3 原有 ORD-1001 requested 保留；三个自有 HTTP 进程已停止，PostgreSQL/卷保留。详细命令、限制和证据见 [P4_VALIDATION.md](P4_VALIDATION.md)。P4 到此停止，P5–P6 保持 NOT_STARTED。

## 6. P5 — 单一演示工作台

### 目标与改动范围
Harness 新增/整理静态 workbench，复用 SupportOps HTML/CSS/JS 资产与交互，不新增 React/Vue/Streamlit 必需启动项。
一个地址 `http://127.0.0.1:8010`；页面由 Harness 提供，浏览器只请求 Harness 同源 API。
必要新根文件：`start.cmd`、`stop.cmd`（薄封装，不内置秘密）。

### 操作顺序
1. 固定四个视图：客户服务、运营工单/审批、数据与报表、运行记录；视图按当前 demo 身份展示。
2. 页面显著显示“模拟数据 / 本地演示 / offline 或 live”，身份切换不冒充注册登录。
3. 客户页面显示实际订单、明确确认按钮、工单对话；服务端 ACL 决定权限，不只隐藏按钮。
4. 运营页面提供真实分派/回复/审批；数据页面提供快照刷新和五个模板报表，不生成虚构趋势图。
5. run 视图显示真实步骤/耗时/错误/关联业务 ID；不显示其他客户 run 或秘密。
6. 确认后的副作用与分析快照新鲜度分开显示；空数据/服务失败/unknown_outcome 均有明确状态。
7. 端口冲突在 doctor 阶段失败；start 不自动安装依赖，stop 只处理项目拥有 PID。
8. 关键按钮双击防护只是体验；后端领域幂等仍必须独立生效。

### 验证与证据
```powershell
conda run -n agentopshub python scripts/dev.py demo-check --group ui
```
HTTP/静态测试验证实际 API 绑定；人工浏览器逐项完成六个剧本，记录截图/步骤及真实 run_id。
不能只证明页面返回 200；需实际点击确认、切换角色、回复工单、刷新并看到分析改变。
覆盖 AC-03 及 AC-04–AC-09 的可见交互。截图仅用模拟数据，不伪造 UI 成功状态。
回退只停用新静态入口，不破坏服务内既有 UI 或领域 API。

### P5 实际状态（2026-10-02）

规定 `demo-check --group ui` exit 0：5 单测、1 真实 HTTP/PG 跨服务集成；实际浏览器六个剧本完成，截图/DOM/真实 run_id 见 [P5_VALIDATION.md](P5_VALIDATION.md)。Harness workflow 32、SupportOps safety 153、根边界 27、P4 reports 1 passed；本批无 skip。9 Python 文件 lint/format、3 改动源码 strict mypy、JS syntax、Contract、698 文件来源保护及 diff 检查通过。start.cmd/stop.cmd 实测通过；占用 8010 时 doctor 拒绝且未启动。

四个同源视图复用 SupportOps 静态风格和七个固定 workflow；实际工单双向回复、低/高金额确认、服务端权限与运行隐私、坏快照及下游失败可见。高金额 UI 先运营审批再客户确认。业务提交与快照新鲜度独立；刷新前后实际统计变化。运营 detail 复用既有授权列表，不新增领域路由。无新框架/依赖/安装/live 请求/commit/push。测试 schema 遗留 0，主库状态与 P4 一致；自有 HTTP 已停止，PG/卷保留。P5 停止，P6 NOT_STARTED。

P5 审阅修复（2026-10-02）：已确认业务结果与记录失败分别显示，工单回执避免重发；故障备份按测试会话隔离。补充并接入 UI gate 的回归通过（7+5+真实 HTTP 1），Harness 39、根边界 32 passed；实际连续两轮三服务/PG corrupt/repair/finish 通过并保留旧证据。详见 P5_VALIDATION 的审阅修复节。P6 未开始。

## 7. P6 — 验证、演示手册与面试说明

### 目标
将“可以运行”转为“可展示、可解释、有边界”，完成后停止主线扩张。
新增 `demo/scenarios/e2e-v1.json`、`tests/integration/test_demo_v1.py`、`docs/DEMO.md`、`docs/INTERVIEW.md`、实测摘要。

### 24 个固定跨模块场景
以下是 24 个逻辑场景 ID；参数化测试可能生成更多 pytest case，不能混淆两种计数。
| ID | 场景 | 判据 |
|---|---|---|
| E01 | 新目录导入/启动隔离 | 解释器/源码路径正确，原仓库/原 DB 不改 |
| E02 | fixture 复现 | 固定输入两次生成同 hash、行数与主键正确 |
| E03 | 重复导入/误重置 | 不重复累加；非自有资源零写入 |
| E04 | 自己订单查询 | 返回正确订单与金额 |
| E05 | 他客户查询 | 不返回他人信息 |
| E06 | 他租户查询 | 不返回租户外信息 |
| E07 | 知识有证据 | 来源可追溯，offline 不调用 LLM |
| E08 | 知识无证据 | 拒答或转人工，不编造政策 |
| E09 | 低金额准备 | 未确认不提交 |
| E10 | 客户确认 | 本地操作提交一次、回执真实 |
| E11 | 重复确认 | 不产生第二次业务提交 |
| E12 | 高金额未审批 | 保持待审批，不可偷跑 |
| E13 | 审批与确认分离 | 客户不能审批；授权审批及确认满足后才提交 |
| E14 | 取消/过期/报价漂移 | 分别拒绝非法状态转换，不复用旧报价 |
| E15 | 人工工单往返 | 分派、人工回复、客户回复都持久化 |
| E16 | 客户访问运营能力 | 报表、导出、他人运行详情被拒绝 |
| E17 | 真导出→真消费 | 四数据集 producer/consumer 通过，非自造响应 |
| E18 | 快照刷新 | 业务变化可在新快照体现；旧快照不变 |
| E19 | 五种报表 | 与独立 oracle 的行/计数/金额相符 |
| E20 | 坏交付 | hash/schema/tenant/path 等坏包失败且不替换有效快照 |
| E21 | SQL 安全 | 非法语句/越界列/外部访问/超限制被阻断 |
| E22 | 下游失败与写入未知 | 真实/注入故障可见，写入不自动重放 |
| E23 | 运行隐私与身份 | 日志无秘密/正文，run 访问按主体隔离 |
| E24 | 离线与停启 | 无模型下载/调用，非必需依赖关闭仍工作，停止仅影响自有资源 |

### 必须执行的既有回归范围
SupportOps：auth、tenant、operations、workflows、ticket messages、业务数据 Contract/export/delivery；PostgreSQL 场景不以 SQLite 代替。
DataCopilot：business catalog、managed SQL、delivery、DuckDB、identity、governed API 与既有确定性业务分析评测。
Harness：新增 fixed workflow、adapter、run store、demo identity；改到的原 runtime/tool 代码需跑对应既有测试。
未改且依赖可选模型/检索服务的历史评测可列非默认；不得删除用例、降低原断言或抹去失败。
分服务记录 pass/fail/skip 与执行环境；根入口必须聚合退出码，不能某服务失败而总体仍返回成功。

### 验证命令（P6 实现后执行）
```powershell
conda run -n agentopshub python scripts/dev.py verify --offline
conda run -n agentopshub python scripts/dev.py demo-check --all
```
verify 应运行必要服务回归 + 24 跨模块场景；`demo-check --all` 输出 AC 到 run/test evidence 的映射。
本轮形成文档时不能预先写“24/24 通过”。

### 交付文档
DEMO.md：前置检查、唯一启动/停止命令、角色、六个剧本、预期界面/状态、模型不可用的诚实替代方式、已知限制。
INTERVIEW.md：业务问题→边界→技术选择→替代方案→失败语义→测试证据；含可复用的项目讲述结构。
必须能回答：为什么不是纯 Agent？为什么三进程不直接单体？为什么 PG + DuckDB？为什么用 Contract？为什么暂不用向量库/MCP/Kafka？
说明 BM25 的局限、Float 金额遗留、快照非实时、demo 身份非生产登录、run log 非恢复引擎。
简历只能使用实际完成的职责和实际测得数字，不承诺生产部署/高并发/零幻觉。
可选 live 模型结果单列：模型/配置、样本数、成功失败、usage/cost 可知性；没有运行就写 NOT_RUN，不阻塞 offline demo 完成。

### 结束规则
AC-01–AC-12 均有证据后将进度板标为 COMPLETE，README 标识 `v1-demo-ready`。
只在用户要求时创建 Git commit/tag/push；状态名不表示已自动创建 Git tag。
不再追加 P7、P8 等主线；只将潜在增强记录为不阻塞演示的后续想法。

### P6 实际状态（2026-10-02）

规定 verify --offline、demo-check --all 均 exit 0；24 逻辑场景 passed、0 failed/skip；根 41、Harness 291、SupportOps 219/1 skip、DataCopilot 284/1 skip，真实 HTTP support 1、delivery 6、reports 1、ui 1 passed。既有离线 D5 50/50 通过；计数有重叠，不重复累加。七个改动 Python 文件 lint/format、语法检查、Contract、698 来源保护和 diff 审阅通过。根/SupportOps 新脚本未配置类型检查，未改 Harness typed 源码，未宣称全量 mypy。

保留两轮失败及修复证据：故障后改为只读 PG 核对；修复 Windows 已退出 PID 的身份读取，未知/活进程仍 fail-closed。public 业务行指纹前后相等，测试 schema 0，HTTP 均停止，PG/卷保留；无 live 模型/安装/commit/tag/push。手册、面试说明和完整 AC→test/run 映射见 [P6_VALIDATION.md](P6_VALIDATION.md)。P6 COMPLETE，v1-demo-ready，剩余批次 0，到此停止。历史各阶段的“P6 未开始”保留为当时记录。

## 8. 每批统一执行模板

将下列模板交给执行器，并把 Pn 替换为这次唯一批次：
```text
工作目录 D:\Projects\WorkflowOps。
先读 docs/SPEC.md、docs/SOURCE_AUDIT.md、docs/PLAN.md。
本次只执行 Pn，不自动开始下一批。
先读真实源码并核对前置状态，保护原仓库的未提交/未跟踪文件。
按 Plan 的目标路径实现最小改动；保持 Workflow-first 和服务边界。
使用每个服务原有 Conda 环境；禁止跨服务 import、模型下载、未授权 live 调用。
新增或变更的行为先有测试，再验证真实闭环；不伪造数据响应或通过记录。
不修改原项目，不自动 commit/push，不操作未知数据库或进程。
完成后更新本批状态并汇报：文件、命令、实际结果、未验证项、剩余批次。
遇阻塞只处理本批最小必要问题，不追加基础设施或新的大阶段。
```

P1 的授权范围仅是安全复制、目录/脚本/自有演示配置，不包括清理原仓库、提交历史、迁移原数据库或统一依赖。

## 9. Spec → Plan 追踪

| Spec 内容 | 实施批次 | 主要验收 |
|---|---|---|
| 单目录、单仓库、Conda 与来源安全 | P1 | AC-01 |
| 模拟数据与领域事实分离 | P2 | AC-02 |
| 确定性流程、身份、退款/工单、运行记录 | P3 | AC-04/05/06/09 |
| Contract、快照发布、受控报表 | P4 | AC-07/08 |
| 单入口与可见错误/权限状态 | P5 | AC-03 |
| 离线、回归、完整演示、技术讲述 | P6 | AC-10/11/12及全部交叉验证 |

技术或路径存在不确定时，以最小源码核对补足，不借此升级目标。新增 API 名称可在首个实现批次冻结，但语义边界不得放宽。

Post-P6 仓库整合：物理服务目录已整理为 `services/harness`、`services/support`、`services/analytics`；原 CLI 标识和 Conda 环境保持兼容。当前运行与历史路径说明见 [Monorepo 指南](MONOREPO.md)；本次仅仓库整合，P1–P6 开发状态不变。
