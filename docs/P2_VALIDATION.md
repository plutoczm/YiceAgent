# P2 模拟数据与领域导入：验证记录

日期：2026-10-01（Asia/Shanghai）。执行范围仅 P2；P3–P6 未开始。

## 前置状态

P1 的完成记录、PLAN 和相关 SPEC 已核对。开始时根 Git 为独立仓库，P1 基线提交 `d7971be`；`git status --short`、工作区 diff 和暂存区 diff 均为空。
完成时再次运行 `scripts/verify_import.py`：698 个原始源/目标文件哈希一致，三个原仓库 HEAD/branch/status 未变，无嵌套 Git，也未复制秘密或数据库。

用户明确授权创建、启动和验证本次 WorkflowOps 专用 PostgreSQL 资源，必要时准备固定镜像，并迁移/导入及重置本次新建的演示库。未操作原项目数据库、容器或卷。

## 实现与文件

- `contracts/demo_fixture/v1/schema.json`：封闭的 Pydantic JSON Schema；加载时与领域模型核对，拒绝漂移。
- `demo/scenarios/default.json`：低金额、高金额、500 元边界、不可退、他客户、他租户和人工咨询输入。
- `demo/expected/fixture-v1.json`：冻结的字节复现基线，仅用于测试，不充当业务 API 或报表响应。
- DataCopilot `scripts/generate_demo_data.py`、`tests/test_demo_data.py`：标准库生成器及复现测试；不连接业务数据库、不导入 SupportOps ORM。
- SupportOps `app/demo/{__init__,fixtures}.py`、`scripts/{prepare_demo_db,import_demo_fixture}.py`、`tests/test_demo_data.py`：完整输入校验、归属标记、原有 Alembic 迁移、事务导入及真实 PostgreSQL 验证。
- 根 `scripts/demo_db.py`、`scripts/dev.py`、`tests/test_p2_boundaries.py`：固定资源归属检查、`seed-demo` 和 `demo-data` 测试分派。
- README、PLAN 和本文：仅更新 P2 状态、操作入口与实测证据。

生成器使用独立 `random.Random(seed)`、固定列表遍历、固定参考时间和排序 JSON。版本为 `workflowops-fixture-1.0`，默认 seed 为 42，参考时间为 `2026-10-01T00:00:00Z`；2 租户、每租户 3 客户标识及 12 订单，共 24 订单，币种 CNY、金额整数元。

SupportOps 复用 `OrderRow`、`SupportStore`、`SupportWorkflows`、`TicketWorkflow`、`ActionPolicy` 和现有五步 Alembic 迁移；没有改写已有领域代码。先完整校验 schema、版本、日期、主键、引用、金额、分布、场景语义及 manifest/hash，再执行单事务导入。数据库 advisory lock 序列化导入；相同 hash 返回明确 no-op。非空目标或不同 hash 默认拒绝；显式 reset 只删除本项目已有领域表的内容，遇未知表拒绝，失败回滚删除与插入。

根入口分别用 `datacopilot` 和 `supportops-ai` 环境执行生成/导入，不跨服务 import。使用固定 loopback DSN，不接受 CLI DSN；拒绝外部主机、不同数据库/用户/端口及查询参数。Docker 容器/卷必须同时具有 WorkflowOps owner 和 Compose project 标签；容器镜像、挂载卷和端口也须匹配。数据库单行归属/hash 标记为 `workflowops_demo_owner`，属于演示管理元数据，不是第二套业务状态源。

## 实际数据库与复现证据

专用资源为 `workflowops-demo-postgres-1` 和 `workflowops-demo_workflowops_demo_pgdata`；镜像 `postgres:16.13-trixie`，数据库/用户 `workflowops_demo`，仅 `127.0.0.1:55432`。首次创建后通过五步 Alembic 升级到 `20260917_0005`，实际导入 24 订单。

fixture 字节数 7079，SHA-256：

```text
99336a83037b42cb31028b34fb08a22f71b2f0d6c39284e6e2b7e8cef1fdf305
```

生成器测试在两个独立目录生成相同 fixture 与 manifest，并与冻结 golden 字节哈希比较；全局 random 状态保持不变。连续两次根 seed 命令均返回 `status=no-op`、同一 hash、`order_count=24`；显式 `--reset-demo` 返回 `status=imported`、同一 hash、24 订单。

工单和动作仅在测试拥有的随机 PostgreSQL schema 中通过既有领域 workflow 创建：低金额待确认、高金额待审批、500 元边界待确认、不可退订单拒绝、人工咨询产生 open 工单。未伪造审批或退款回执。测试比较状态、金额、计数及真实查询；UUID 只检查合法生成和彼此不同，运行时间按测试开始/结束及 300 秒 TTL 范围检查，不使用 fixture 参考时间替换业务时钟。测试 schema 在结束后清理；主演示库保留 24 个初始订单，不预置已退款终态。

## 命令与结果

以下根命令均通过现有 `agentopshub` Conda 环境运行，由 dev.py 分派对应服务环境。执行日志和汇总位于 ignored `.runtime/p2-validation/`。

```text
conda run -n agentopshub python scripts/dev.py seed-demo --seed 42
conda run -n agentopshub python scripts/dev.py seed-demo --seed 42
conda run -n agentopshub python scripts/dev.py seed-demo --seed 42 --reset-demo
conda run -n agentopshub python scripts/dev.py test-service supportops --group demo-data
conda run -n agentopshub python scripts/dev.py test-service datacopilot --group demo-data
conda run -n agentopshub python -B -m pytest -q
conda run -n agentopshub python scripts/dev.py check-contracts
conda run -n agentopshub python scripts/dev.py test-service supportops tests/test_s1_workflows.py tests/test_business_operations.py tests/test_tenant_boundary.py tests/test_business_data_contract_v1.py tests/test_business_data_delivery_v1.py tests/test_business_data_export_v1.py -rs
python -B scripts/verify_import.py  # 实际使用既有 agentopshub 绝对解释器
```

| 验证 | 实际结果 |
|---|---|
| P2 SupportOps | 21 passed，0 skipped；含真实 PostgreSQL 导入、并发、no-op、重置、回滚和领域场景 |
| P2 DataCopilot | 3 passed，0 skipped；复现、分布、场景及随机状态 |
| 根测试 | 最新 13 passed，0 skipped；8 项 P1 与 5 项 P2 资源边界；初始批次为 12 passed |
| 既有 SupportOps 相关回归 | 169 passed，1 skipped；Windows 不能创建真实 symlink，不计为通过 |
| Contract | 根/producer/consumer/acceptance 哈希一致，原协议未修改 |
| 原项目保护 / verify-import | 698 源/目标文件哈希一致，三原 Git 状态一致 |
| Ruff lint / format | 所有新增 Python 文件和修改的 dev.py 均通过 |
| Git diff 检查 | 无空白错误；差异仅 P2 实现、测试和文档 |

负例覆盖坏金额/浮点金额、重复订单、客户/租户错配、未知字段、版本/日期/场景错误、fixture hash 不符、外部 DSN、非空但无导入 hash、未知表、错误数据库归属及未知容器/卷。真实 PostgreSQL 故障注入发生在 reset 删除后、插入前，事务回滚后订单和原 hash 均保留；错误归属 reset 后订单数未变。未知 Docker 资源负例使用确定性测试，未尝试改写用户的真实资源。

实现收敛时 Ruff 曾发现字典写法与长行问题，修复后全部通过。最终补充了导入函数对模型数据的再校验，重新执行 SupportOps 21 项测试通过；不将此前失败写为通过。新增改动没有配置独立类型检查器，原 Harness 类型配置不适用于这些未改动的服务代码；不声称全仓类型检查通过。

## 限制与停止点

AC-02 已按 P2 验证；不代表 P3 HTTP workflow、P4 Delivery/分析或后续 UI 闭环完成。原模型/外部依赖未加载，没有安装依赖、修改用户配置、提交或推送。

本地新生成的数据库配置/密码、fixture、日志仅位于 ignored `.runtime/`。专用 PostgreSQL 容器当前运行，卷与数据保留；未实现 P3 demo-up/down，也未停止其他项目进程。后续若要清理，必须先核验本次资源所有权，不直接删除未知资源。

P2 到此停止。P3–P6 保持 NOT_STARTED。

## P2 审阅修复与验证（2026-10-01）

审阅发现继承的 `COMPOSE_PROJECT_NAME` 可以覆盖 Compose 文件中的项目名，使启动目标与前置归属检查的资源不一致。已在实际 `compose up` 命令显式传入 `--project-name workflowops-demo`，并新增回归测试验证：即使继承其他项目名，启动命令仍固定到 WorkflowOps。

实际 Docker Compose 只读配置解析在 `COMPOSE_PROJECT_NAME=review-scope-probe` 条件下通过：解析项目名仍为 `workflowops-demo`，卷名仍为 `workflowops-demo_workflowops_demo_pgdata`。随后在同样的进程环境覆盖下执行根 `seed-demo --seed 42`，exit 0；实际演示库返回相同 fixture hash、`status=no-op`、24 订单。日志为 ignored `.runtime/p2-validation/review-fix-seed.log`。没有创建 probe 项目、重置数据或修改用户级环境配置。

README 的 P2 命令代码块已恢复标准三反引号 `text` 围栏，并恢复内联代码标记；检查围栏配对和无意外制表符通过。根测试最新为 13 passed，新增资源覆盖回归在内；改动 Python 文件 Ruff lint/format 与 `git diff --check` 均通过。本次仅修改启动参数、测试和文档，未重复运行未受影响的服务领域测试；此前服务测试结果仍为前述实测记录。

提交前发现现有 Git `core.autocrlf=true` 会在 Windows 再次检出时改变 golden fixture 的字节。新增根 `.gitattributes`，仅固定 golden fixture 与 demo schema 为 LF；schema 工作树也规范化为 LF，不修改用户 Git 配置或既有 Business Contract。已核对 Git 属性和暂存 blob/工作树字节一致；重新执行 DataCopilot 3 项、SupportOps 21 项测试通过，seed 返回同一 fixture hash、no-op、24 订单。schema 的规范化字节哈希为 `00f28bf99d0b10f80a7b0d8ec6996a09967522b6e14d4ec281df49279de9fbbc`；manifest 已重新生成，fixture 与业务数据未改变。
