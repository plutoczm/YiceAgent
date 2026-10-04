# P3 固定工作流与客服 HTTP 闭环：验证记录

日期：2026-10-01（Asia/Shanghai）。仅执行 P3；P4–P6 未执行。

## 前置与范围

已检查 AGENTS、PLAN 的 P2 完成记录与 P3 要求、P2_VALIDATION、SPEC 相关接口/身份/超时/验收边界及 SOURCE_AUDIT 的对应实现基线。开始时 HEAD 为 `9119478`，工作区与暂存区 diff/status 均为空。P2 为 COMPLETE；本次没有重新导入或重置主演示库。

复用原 SupportOps 的 Principal、SupportStore、SupportWorkflows、BusinessOperations、ActionPolicy、TicketWorkflow、SupportOrchestrator、KnowledgeService、BM25 和现有 HTTP 路由。原生产 JWT/JWKS 验证、审批要求和领域状态机未改；原 Harness AgentRuntime 未用于主流程。

## 文件与架构

- Harness `backend/src/app/workflows/{models,engine}.py`：五个固定 workflow、封闭 Pydantic action DTO、类型化结果与错误枚举，LangGraph 固定 validate → authorize → invoke_domain → format_record，权限失败直接记录并结束。
- Harness `backend/src/app/adapters/{supportops,supportops_dto}.py`：固定 loopback URL、路由白名单、响应 DTO 校验；沿用 httpx2，关闭代理、重定向和自动重试。wire DTO 为领域 DTO 的静态快照，测试核对 AST 字段一致，不跨服务 import。
- Harness `backend/src/app/demo/{identity,composition}.py` 与 `api/workflows.py`：白名单身份、不可猜测 HttpOnly session、切换撤销旧 session、Host/Origin 检查；demo factory 只构建 HTTP client、工作流和 run store。
- Harness `backend/src/app/runs/store.py`：每 run 独立 JSONL，记录实际步骤、耗时、终态和领域 ID；客户只读自己的 run，运营只读本租户。尾行损坏返回 incomplete。结果正文、消息、SQL、token 不入日志。
- SupportOps `app/demo/{identity,composition}.py`：独立 opaque token 校验与 demo-only Principal 子类；只有固定管理员身份可走审批。子类明确使用 `demo-opaque`，不假称通过了 JWT 验证。已有生产 Principal 保持原要求。
- SupportOps 新增 `/internal/demo/actions`：明确退款/人工接管输入；退款先检查订单归属，再调用既有 preparation；人工接管调用既有工单流程。自然语言消息仍复用 `/v1/support/messages`。
- SupportOps offline composition 只构建 BM25，保留原知识引用与证据不足拒答；不构建哈希 embedding、向量库、LLM 或 Redis。
- 根 `scripts/dev.py`、`demo_runtime.py`、`demo_service.py`、`demo_process.py`：demo-up/down、测试分派，固定 cwd/PYTHONPATH 和原 Conda 解释器；隐藏启动子进程、仅 loopback 绑定。停机前校验全部 PID、可执行文件、创建时间、启动标记；终止句柄上再次核验创建时间。访问被拒绝不当成进程已退出。端口占用不接用、不杀占用者。
- 根 `scripts/p3_integration.py` 与 SupportOps `scripts/p3_test_database.py`：在已有自有 PostgreSQL 中创建随机 `p3_test_<uuid>` schema，用实际 fixture importer 装入 24 订单；真实 HTTP 测试后核验归属并清理该测试 schema，恢复之前运行的自有 demo 服务。
- `scripts/export_p3_contract.py`、Harness `scripts/export_p3_api.py`、`contracts/service_api/v1/p3.json`：wire 快照导出与已实现请求/响应/日志机器契约，不修改 Business Contract。
- 测试：Harness `backend/tests/test_p3_workflow.py`、SupportOps `tests/test_p3_demo.py`、根 `tests/test_p3_process_boundaries.py` 与 `tests/integration/test_p3_support.py`。更新 README、PLAN 和本文。

等待确认/审批时当前 run 结束。下一人工动作产生新 run，可提供同权限范围内的 parent_run_id。确认请求按既有 SupportOps v1 契约直接记录 operation_id=action_id，省略 parent 时也保留核对线索；parent 只做关联和归属检查，不提供或覆盖当前 operation_id。领域幂等仍由 SupportOps 提供，JSONL 不承担持久恢复。

## 实际命令与结果

以下从根目录通过既有 agentopshub 环境执行；服务测试由入口分派到各自环境。首次验证日志在 ignored `.runtime/p3-validation/`，审阅修复后的最新逐项退出码与日志在 `.runtime/p3-review-fixes/`，真实 HTTP 的 26 个 workflow run 元数据在 `.runtime/p3-http-evidence.json`。

```text
conda run -n agentopshub python scripts/dev.py demo-up --mode offline
conda run -n agentopshub python scripts/dev.py test-service harness --group workflow -q
conda run -n agentopshub python scripts/dev.py test-service supportops --group safety -o addopts= -q -rs
conda run -n agentopshub python scripts/dev.py test-integration --group support
conda run -n agentopshub python -m pytest tests/test_p1_boundaries.py tests/test_p2_boundaries.py tests/test_p3_process_boundaries.py tests/test_p3_integration_cleanup.py -q
conda run -n agentopshub python scripts/dev.py check-contracts
conda run -n agentopshub python -B scripts/verify_import.py
conda run -n agentopshub python scripts/dev.py demo-down
```

| 验证 | 最终实测结果 |
|---|---|
| Harness P3 | 26 passed，0 skipped；DTO/授权/步骤/会话/归属/parent/隐私/超时/故障/重定向/wire parity |
| SupportOps safety | 153 passed，0 skipped；4 项 demo 与 5 项真实 PG 清理测试及原 auth、tenant、business operations、ticket messages、S1 workflows 回归 |
| 真实 HTTP + PostgreSQL | 1 passed，0 skipped；一个组合闭环，26 次真实 workflow run；没有 MockTransport |
| 根 P1/P2/P3 边界 | 23 passed，0 skipped；13 项原测试、6 项进程边界与 4 项准备失败清理测试 |
| Ruff lint / format | 最新 28 个改动 Python 文件全部通过 |
| Harness 类型检查 | 配置的 strict mypy 检查 13 个本次源码/导出文件通过；未宣称全仓/全部测试类型检查通过 |
| Business Contract | root/producer/consumer/acceptance 原字节哈希一致 |
| 原项目保护 | 698 源/目标哈希一致，三个原仓库 HEAD/branch/status 一致，无秘密/DB复制或嵌套 Git |
| diff | `git diff --check` 通过；改动只涉及 P3 实现、契约、测试与进度文档 |
| 启停 | 真实启动与所有权核验停机通过，最终 8010/8011 空闲，owned process state 已移除；数据库/卷保留 |

mypy 通过服务分派在 Harness cwd 使用原 `pyproject.toml` 的 strict 配置，目标为本次 adapters、demo、runs、workflows、api/workflows 和 scripts/export_p3_api；`--follow-imports=silent`，缓存指定到根 ignored `.runtime/mypy-harness`。未安装或升级依赖，未修改用户级配置。

## 验收证据与故障覆盖

真实 HTTP 演示低金额 preparation → pending_confirmation → 客户确认 → submitted → 重复确认。重复确认后 operation 版本未增加。高金额进入 pending_approval；客户尝试批准失败且状态/版本未变化；运营审批后仍待客户确认，随后由客户提交。界面/接口不宣称支付到账。

真实工单经历 open → assign → 运营真实输入回复 → 客户真实输入续回复；messages API 返回 agent/customer 消息，工单回到 assigned。工单只存在 SupportOps。知识查询返回实际 BM25 片段、source_uri 和 chunk_id。

错误 token、session 作为错误服务凭据的同类负例、客户伪造角色、他客户/他租户订单与工单、他人 run、跨身份 parent、未知 workflow 和权限字段注入均被拒绝。高金额越权审批后领域状态/版本不变，隐藏对象不会创建退款操作；原领域测试覆盖取消、过期、漂移、审批与确认分离等状态规则。

MockTransport 仅用于 Harness 故障注入：读超时为 failed，写超时/丢响应为 unknown_outcome，不重放，保留已知 operation_id；总体 60 秒截止路径同样验证。HTTP client 单次调用同时设置 30 秒整体截止和传输超时。重定向不会跟随；下游正文/异常内容不进入公开错误或 JSONL。真实 HTTP 闭环另行执行，不用 mock 替代。

## 已纠正的失败与最终资源状态

早期 HTTP 验证错误地把提交后 operation version 写死为 2，实际领域实现为 3；改为验证重复确认前后版本不变。早期同时执行多个 Conda wrapper 命令曾使一次 probe exit 3；改为顺序执行，最终每条规定命令均 exit 0。曾有 Ruff/类型错误，修复后再验证通过。

第一次原项目保护检查因 mypy 在 Harness 内生成 `.mypy_cache/3.12/cache.db` 失败。已将本次缓存保留移至 ignored `.runtime/`，后续缓存显式指定到 `.runtime/mypy-harness`；最后整批验证的保护检查通过。没有放宽 verify-import 的数据库边界规则。

早期实测已在主演示库确认 `ORD-1001`，该业务提交保留，没有用 reset 抹掉：最终 24 订单、1 pending action 记录、1 receipt、0 工单；低金额 refund_status=requested，高金额 refund_status=none。后续完整集成测试均用独立 schema；最终遗留 P3 测试 schema 为 0。上述统计来自实际只读 SQL，不来自 expected fixture。

两个 P3 HTTP 进程已停止，P2 专用 PostgreSQL 与数据卷保留。停止软件不会撤销数据库业务提交。

## 限制与停止点

AC-04/05/06/09 的 P3 API 部分有证据，不等于 P5 UI 或 P6 全套验收完成。BM25 为词法证据摘录，未实测 live 模型质量；opaque 演示身份不是生产登录。进程管理为本机 Windows、单 worker；停机使用经所有权核验的进程终止，不承诺生产级优雅停机或分布式恢复。

P3 不启动 DataCopilot API，不实现 exports、reports、快照刷新、SQL 分析或 UI。未修改三个原目录、未 commit/push。P3 完成后停止，P4–P6 保持 NOT_STARTED。


## P3 审阅修复（2026-10-01）

本次仅修复已审阅的三项 P2 问题；沿用此前未提交的 P3 实现，没有进入 P4，也没有提交或推送。

1. 确认超时的核对 ID：`workflows/engine.py` 直接按领域契约保存 `operation_id=action_id`，不依赖可选 parent。parent 的 run 归属校验保留；即使 parent 元数据含无关 ID，也不会替换当前确认对象。无 parent 的超时、新 run/持久记录以及真实 HTTP 确认均有回归。
2. 未知写入分类：`adapters/supportops.py` 增加每次 run 独立的内存 DispatchState；在路由与 token 准备完成、开始调用 HTTP client 时记录写请求派发，并在得到确定领域响应后标记结果已知。`engine.py` 的一般异常及整体截止仅将“已开始写请求且结果未确定”标记 unknown_outcome。派发前日志失败、只读异常/截止返回 failed；已确认提交但 run 记录失败时保留实际 typed result，避免丢失已知业务结果。不记录该内存对象或结果正文到 JSONL，不自动重放请求。
3. 部分 schema 准备失败清理：`p3_test_database.py` 将 CREATE SCHEMA 与 schema 内归属标记放在同一事务，建表、fixture 校验或导入失败后清理本次自有 schema。drop 对不存在的 schema 为 no-op，存在时必须有唯一正确归属标记；未知/错误归属拒绝删除。`p3_integration.py` 在准备返回非零或抛出异常时仍执行外层清理，覆盖准备进程中断的路径。

回归改动：Harness `test_p3_workflow.py` 新增/扩展无 parent 确认超时、派发前失败、读/写未预期异常、派发前/后整体截止、已知提交后的记录失败；根新增 `test_p3_integration_cleanup.py` 的 4 个退出/异常和恢复组合；SupportOps 新增 `test_p3_database_cleanup.py` 的 5 个真实 PG 故障与归属案例，并纳入 safety 测试组。真实 HTTP 组合测试的低金额确认改为省略 parent，验证返回 ID 与实际可查询 operation 一致。

整批 P3 命令重新执行全部 exit 0：Harness 26 passed、SupportOps safety 153 passed、真实 HTTP 1 passed（26 次 workflow）、根测试 23 passed，均无 skip。28 个 Python 文件 Ruff lint/format、13 个本次源码的 strict mypy、Contract、698 文件保护检查和 diff 检查通过。表格已更新为审阅修复后的最新结果；初次 P3 结果为 19/148/1/19，此前记录不代表覆盖了这些新失败路径。

真实 PG 故障测试证明 fixture bytes/hash 不符、建表异常、import 异常后 schema 不遗留；外层重复 drop 可安全 no-op。缺失/错误归属 schema 在实现中被拒绝删除，由创建它们的测试 fixture 自行回收。主演示库订单数保持 24。

最终只读 SQL：24 订单、0 工单、1 pending action、1 receipt，ORD-1001 refund_status=requested、ORD-1002 refund_status=none；遗留 P3 测试 schema 为 0。两个自有 HTTP 服务已停止，PostgreSQL/卷与原有申请保留。数据库统计在 ignored `.runtime/p3-review-fixes/database-final.json`。没有修改原三项目源码/数据库、安装依赖、修改用户级配置或宣称完成 P4/UI/live 模型验收。


## P3 本地提交授权（2026-10-01）

三项修复通过验证与只读复审后，用户明确要求提交 P3。本次本地提交包含 P3 实现、API 契约、测试、三项修复及验证文档；不包含 ignored runtime、数据库、秘密或原项目改动。前文未提交的描述记录验证当时状态；本次提交不推送远端、不进入 P4。
