# P5 单一演示工作台：验证记录

日期：2026-10-02（Asia/Shanghai）。只执行 P5；P6 未开始。未 commit/push。

## 前置核对与实现

开始时 HEAD 为 `0fcaadc`（P4 本地提交），git status、工作树与暂存区 diff 均为空。核对 AGENTS、PLAN 的 P4/P5、P4_VALIDATION、SPEC 的六场景/身份/API/UI/启动及 AC-03–09 条款，检查受影响实现。P4 完成证据与提交一致。未修改产品范围。

- Harness `demo/static/{index.html,styles.css,app.js}`：四个视图，复用 SupportOps CSS 主题、DOM textContent 安全渲染、明确确认与工单交互方式。无前端框架或构建依赖。浏览器只调用同源 Harness `/api/demo/session`、七个固定 workflow 和权限过滤的 run 读取；不传下游 Bearer token、任意租户/角色/SQL/路径。
- `demo/composition.py`：根 `/` 提供统一入口，静态资源在 `/workbench/`；原 loopback/Origin 防护保留。增加 no-store、CSP 同源连接、nosniff 和 no-referrer。页面固定标记“模拟数据 / 本地演示 / offline”，身份选择明确非注册登录。
- `adapters/supportops.py`：浏览器发现原适配器运营 detail GET 指向不存在的领域路由。现复用既有、已授权的运营工单列表并筛选 typed ticket，不新增领域路由、不绕过领域 ACL。不存在/不可见对象统一拒绝。`workflows/engine.py` 为工单动作记录已知 ticket_id，仍只保存元数据。
- `scripts/dev.py`、`start.cmd`、`stop.cmd`：薄封装各自环境，start 先 doctor 再启动。doctor 拒绝 HTTP 端口冲突；55432 仅可接受核验归属/版本/卷/绑定且健康的既有 WorkflowOps PostgreSQL。start 不安装、不下载、不重置、不自动 seed；需先按 P2 准备演示库。stop 沿用 PID/可执行文件/创建时间/启动标记核验，只停本项目 HTTP，保留 PG 和业务数据。
- `p3_integration.py` 分派 P5 UI 集成，新增 `test_p5_workbench.py`、`test_p5_start.py`、`test_p5_ui.py`。`scripts/p5_browser.py` 仅供测试：复用既有 importer 和带归属标记的独立随机 schema，坏快照/停下游只操作本次隔离资源；Web 不新增故障注入或数据重置 API。

身份切换清空文本、业务对象、审批版本、报表、确认按钮及本会话 run 索引；后端同时撤销旧 session。不使用 localStorage/sessionStorage 保存客户内容。按钮忙碌锁减少双击，领域幂等独立验证。高金额 UI 先审批再客户确认；运营无法替客户确认。确认后显示“退款申请已提交”，另行提示分析需要刷新。报表展示实际 rows/row_count、template/v1、生成时间、as_of、耗时、snapshot_id、fingerprint 和水位。空数据、失败与 unknown_outcome 单独显示；未知写入无自动重试。

## 最终命令与结果

根命令由既有 agentopshub 解释器执行；`dev.py` 子进程分别使用各服务原 Conda/cwd/PYTHONPATH。日志位于 ignored `.runtime/p5-validation/`。没有安装、模型加载、付费调用或全局环境修改。

```text
conda run -n agentopshub python scripts/dev.py demo-check --group ui
conda run -n agentopshub python scripts/dev.py test-service harness --group workflow -q
conda run -n agentopshub python scripts/dev.py test-service supportops --group safety -o addopts= -q -rs
conda run -n agentopshub python -m pytest tests/test_p1_boundaries.py tests/test_p2_boundaries.py tests/test_p3_process_boundaries.py tests/test_p3_integration_cleanup.py tests/test_p5_start.py -q
conda run -n agentopshub python scripts/dev.py test-integration --group reports
conda run -n agentopshub python scripts/dev.py check-contracts
conda run -n agentopshub python scripts/verify_import.py
start.cmd
stop.cmd
git diff --check
```

| 验证 | 实测结果 |
|---|---|
| P5 规定 UI gate | exit 0：5 单测 + 1 真实 HTTP/PG 跨服务集成，无 skip |
| Harness P3/P4 workflow | 32 passed |
| SupportOps safety | 153 passed |
| 根边界（P1/P2/P3/P5） | 27 passed，含已有 stop 所有权/全量预核验边界 |
| P4 真实报表回归 | 1 passed，五个独立业务 oracle |
| 新 P5 真实集成 | 重复确认不重复提交；高金额审批后仍需客户确认；真实工单分派/get/双方回复；五报表与独立 PG oracle 一致；坏快照拒绝后修复可查询 |
| Ruff lint / format | 9 个本次 Python 文件通过；Harness 在自身 cwd 使用配置规则（测试 docstring 忽略按配置生效） |
| strict mypy | 3 个改动 Harness 源文件通过；未声称全仓类型检查 |
| JS | bundled Node `--check` exit 0；实际浏览器无 JS console error |
| 启动/停止 | cmd 包装实测 exit 0；占用 8010 时 doctor/start exit 1 且无进程状态产生；stop 不停 PG/未知进程 |
| Contract / 来源保护 | Contract 哈希一致；698 源/快照文件及原三仓 HEAD/branch/status 未变 |
| diff | `git diff --check` exit 0，审阅全部源码与新增文件；仅 Windows Git LF/CRLF 提示 |

`results.json` 保留首轮 Harness lint 从根 cwd 执行造成的测试路径忽略不生效（exit 1）；纠正为服务 cwd 后 `final-results.json` / `lint-harness-cwd.log` 为通过证据。另有初轮测试命令先于测试文件保存、以及高金额测试先确认导致审批立即提交的失败，均修正后完整跑规定 gate。浏览器发现运营 detail 路由缺口后修复并补回归，非只验证页面 200。

## 六个浏览器剧本与真实 run_id

通过 Codex in-app browser 实际选择角色、填字段、点击按钮。测试库使用专用 schema 导入真实 24 个模拟订单；主演示库未 reset。浏览器证据在 ignored `.runtime/p5-browser/evidence.json`、`run-summary.json` 与 JPG。截图/DOM 为真实页面，仅模拟数据；不是设置 DOM 状态或伪造 API 响应。浏览器页面在同一个 `http://127.0.0.1:8010` 完成；不通过三个服务网页拼接。

| 场景 | 实际步骤 / 判据 | run_id / 截图 |
|---|---|---|
| WF-01 准备 | 测试 CLI 复用 importer 导入 24 订单；运营刷新并执行订单报表，tenant-a=12；Web 不提供任意导入/删除 | `655d65bf2d2b410586be8dcac9de120c`、`8fb162f1c7b6453789df78584aa201dd`；01-data.jpg |
| WF-02 查询/证据 | A1 查 ORD-1001，199 CNY；输入 refund policy，返回实际 BM25 原文与 supportops://policy/refund / version / chunk | `3c012362d504409d8287b745cca62a8f`；02-knowledge.jpg |
| WF-03 确认/审批 | 低金额先显示 customer_confirmed=false；实际双击明确确认，ACT-72A3BEEEAA25 仅一个 confirm run。高金额 ACT-60E5958A1489 阻断；运营批准后仍 customer_confirmed=false，客户核对并明确提交 | 低金额确认 `c94a3a73c1924964a6dded6a8a976f2c`、高金额确认 `0804ce5fa5984ef086852c087159c811`；`c99837acaf8147a08483383a8898748e`（批准）、`002770738c324b72b4eed94253cd9d53`（提交后核对）；03-*.jpg |
| WF-04 人工 | 创建 TKT-C143174A1366，运营真实分派/回复；客户读取并回复，消息角色 agent/customer、工单 assigned | `a770e213cff54309a5c23a05329bb528`、`a1ad630d14044e27a903729d76d97c76`、`62dca9eb9922413eb1bd04b8a0bdcc08`；04-*.jpg |
| WF-05 分析 | 首轮空数据；退款与审批后旧快照仍 pending_approval=1 / submitted=1；显式刷新后 submitted=2。五种模板均真实执行，显示水位/as_of/耗时 | `885c6a9ebde9416dae047d94dafa5270`（旧）、`bf7a05ee543140d590ced110472c56ea`（新）；05-*.jpg |
| WF-06 失败/边界 | A2 读取 A1 订单/已知 run 被拒绝，B1 跨租户拒绝；隔离快照 hash 损坏被 consumer 拒绝；只停测试 SupportOps 后只读失败、写请求 unknown_outcome，run 显示 invoke_domain 失败，无自动重放 | `f84dc12a181b48888bd0a155ce294b0a`、`73161fddce374106a31dec83a0b405f4`、`fa3fa54f43e14f788dc38ae7a306e95e`、`09737b5f883e4b15b08f1f3f9a6171f5`、`96061052c76e4af69ef03d454d9a611d`；06-*.jpg |

旧快照 `1e7dee6e2587bee9e427a5fc6e26c9c508d39ffab932faf9a6f4bb9df2352e5c`，新快照 `f230701685b8ebaa51c81578d8f4b66238c8d04762890ed5ccfb917e6499e811`。刷新后业务统计变化来自真实审批/客户确认，非 expected 响应。退款前后单测与实际 API 证明领域幂等；浏览器双击另验证忙碌锁只产生一次 confirm。

浏览器检查默认窄侧栏布局及桌面 CSS 断点；IAB viewport override 的截图画布仍受窄侧栏裁剪，所以 05-desktop-report.jpg 不作为完整桌面视觉验收证据。已复位 override；默认窄屏可滚动完成所有动作，06-final-responsive.jpg 是默认布局截图。未宣称完整跨浏览器兼容。截图/业务日志时间为 UTC，文档日期按 Asia/Shanghai。

## 资源状态与限制

全部自有 HTTP 已停止。PG/卷保留；只读核对主库：24 orders、0 tickets、1 pending_action、1 action_receipt；ORD-1001 requested、ORD-1002 none，与 P4 最终状态一致。隔离测试 schema 遗留 0（database-final.json）。坏测试快照已恢复；ignored 证据/快照保留。原项目 DB/源码/秘密、用户配置未修改。

run 页面索引为当前身份会话最近 60 次执行，刷新/换身份清空；已知 run_id 可继续按后端 ACL 读取持久元数据。日志不是恢复引擎。审批版本冲突/取消/过期由既有领域流程处理，UI 不替代后端规则。未知写入保守显示需要核对，故障断连不证明发生了业务提交。业务动作与快照刷新分开，不声称实时。live 模型未调用，工作台仅 offline；不冒充生产认证/真实支付/生产可用性。

P5 COMPLETE，停止。P6 的 24 场景 verify、DEMO/INTERVIEW 和 v1-demo-ready 尚未执行。


## P5 审阅修复与回归（2026-10-02）

用户在只读审阅后授权修复两项 P2，仍限定 P5，无 commit/push。

1. `app.js`：仅当后端返回 `internal_error` 且携带已确认的 typed result、终态非 unknown_outcome 时，继续处理业务结果，单独积累“业务结果已确认；运行记录失败”告警，并保留至后续只读刷新结束。工单回复先显示真实 ticket_id/message_id 的已发送回执并清空已提交文本，再读取对话；即使后续读取失败，也不将已发送回复当作发送失败。没有结果的错误、领域拒绝和 unknown_outcome 继续报错，不伪造成功、不自动重放。`index.html` 增加两种身份的回复回执区域。
2. `p5_browser.py`：每轮以 `p3_test_<随机 UUID>` 作为独立证据子目录，保存 backup、fault、finished 元数据。活动 state 指针仅用于当前轮；finish 后移入该轮目录。此前根目录旧备份/截图不删除、不覆盖。非法 schema 路径被拒绝。

新增后端两个角色的“回复已确认但记录失败”回归；新增 `tests/ui/p5_interactions.cjs` 执行实际 JS 事件处理器，使用受控 DOM/HTTP doubles 覆盖客户/运营正常回复、已确认结果+记录错误、后续读取失败、未知结果和领域拒绝，10 个交互场景均通过，并断言第二次点击不会重发已清空文本。它属于确定性回归，不称为真实浏览器 E2E。`tests/test_p5_browser.py` 覆盖连续两轮 prepare/corrupt/repair/finish、历史备份保留及非法 session 路径；`test_p5_interactions.py` 用现有 Node 运行回归，无依赖安装。两类回归已接入规定 `demo-check --group ui`。Node 只用于 JS 测试，start.cmd 仍无需前端构建或 Node 服务；测试可通过 WORKFLOWOPS_NODE_EXE 指向既有解释器，或使用现有 PATH/本机 bundled Node。

实际重跑：规定 UI gate exit 0（Harness 7 + 根回归 5 + 真实 HTTP/PG 集成 1）；Harness P3/P4/P5 合计 39 passed，根 P1/P2/P3/P5 合计 32 passed，均无 skip。测试计数有重叠，不重复累加。实际三服务/PG 连续两轮故障验证均完成：新快照查得 tenant-a 12 订单 → corrupt 后真实 consumer 拒绝 → repair 后原 snapshot_id 再可查询 → finish 回收该轮 schema，独立备份/元数据均保留，前次根证据文件 SHA-256 未变。真实 8 个 workflow run 的 ID 与终态见 ignored `.runtime/p5-review-fixes/real-cycles.json`，阶段 stdout/stderr 保存在该目录。

本次五个改动 Python 文件 lint/format 通过，Harness 配置规则通过，三个相关源文件 strict mypy、实际 app.js Node syntax、Business Contract、698 来源文件及原仓状态保护均通过，diff 检查通过。只读最终主库仍 24/0/1/1，ORD-1001 requested、ORD-1002 none；测试 schema 遗留 0，HTTP 和 browser active state 均已清理。一次并发 Conda wrapper 导致临时激活文件锁冲突，串行重跑该只读核对后成功，未修改环境/安装依赖。汇总 `.runtime/p5-review-fixes/summary.json`。

原六个浏览器剧本证据保留；本次未将 DOM doubles 冒充人工浏览器复验，也未重跑无关的 SupportOps 153 项或 P6 全套。两项审阅问题已修复，P5 停止，P6 NOT_STARTED。

## P5 本地提交授权（2026-10-02）

用户在 P5 实施、两项 P2 修复和只读复审完成后，明确授权本地提交 P5，不推送。提交包含工作台、启动/停止入口、测试、进度及验证文档；不包含 ignored runtime、数据库、凭据或原项目改动。上文未提交的描述记录验证当时状态；此次提交不进入 P6。最终复审 12 项测试通过，实际 PG 两轮故障验证沿用已完成证据。
