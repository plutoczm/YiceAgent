# P4 不可变交付与受控报表：验证记录

日期：2026-10-01（Asia/Shanghai）。仅执行 P4；P5–P6 未开始。未 commit/push。

## 前置与实现范围

开始时 HEAD 为 `941f357`（P3 本地提交），git status、工作树/暂存区 diff 均为空。已核对 AGENTS、PLAN 的 P3 完成记录与 P4 要求、P3_VALIDATION、SPEC 对应身份/数据/报表/API/AC-07/08 条款、SOURCE_AUDIT S4/S5，以及实际受影响代码。未重置主演示库，保留 P3 ORD-1001 的申请。

复用 SupportOps exporter、producer validator、REPEATABLE READ READ ONLY 和领域状态机；复用 DataCopilot accepted catalog、consumer、ConfiguredTenantAccessResolver、BusinessAnalyticsContextFactory、ManagedText2SQLPipeline、AST authorizer、governed workflow、DuckDB materializer/executor 与 metadata-only audit。未改 Business Contract v1、SQL 授权策略、生产 JWT/JWKS 默认或原 live Text2SQL 入口；不跨服务 import。

## 文件与工程选择

- SupportOps `app/demo/exports.py`、`composition.py`：受 token/运营权限保护的空对象导出接口；固定 CNY/可信 tenant-a、服务端 UUID 目录、producer 完整验证后原子替换每租户 current 索引。已发布目录不覆盖；导出/校验/索引失败不替换旧索引。索引更新只由 SupportOps 执行。
- DataCopilot `infrastructure/demo_delivery/resolver.py`：封闭索引、指纹绑定、受控根、租户映射、symlink/Windows reparse-point 拒绝；一次请求只固定一个目录，交给独立 consumer 再验证 bytes/hash/schema/tenant/references/budgets。
- DataCopilot `application/demo_reports/service.py`、`composition/demo_reports.py`：五个 report_id → template v1 → 不可信 SQL candidate → 原 D1–D4 管线；demo-only opaque token 映射、固定 grant 与既有 context factory。构造时只加载契约与确定性依赖，无模型初始化/网络调用。业务事实来自交付，非 expected。
- Harness `adapters/analytics.py`、`analytics_dto.py`、`workflows/{models,engine}.py`、`demo/composition.py`：refresh_snapshot/analytics_report，仅运营；独立服务凭据、固定 loopback URL、无代理/重定向/重试、封闭 DTO。run 仅增加 snapshot_id/delivery_fingerprint 关联，不保存 rows/SQL/正文/token。
- 根 `demo_runtime.py`、`demo_service.py`、`dev.py`、`p3_integration.py`：三进程沿用各自 Conda/cwd/PYTHONPATH，health/PID 所有权核验沿用 P3；增加 governed/delivery/reports 分派，复用 P3 自有随机测试 schema 和失败清理。测试快照根单独放在本次启动目录内。
- `export_p4_wire.py`、`export_p4_contract.py`、Harness `scripts/export_p4_api.py` 和 `contracts/service_api/v1/p4.json`：静态 wire 快照和当前 P4 机器契约。原 P3 契约保留为历史阶段快照。
- 新增 DataCopilot `test_p4_reports.py`、Harness `test_p4_analytics.py`、SupportOps `test_p4_exports.py`/`test_p4_snapshot_postgres.py`、根真实集成 `test_p4_delivery.py`/`test_p4_reports.py` 与独立 oracle/pinned-query 脚本。P3 的未知 workflow 负例换为仍未知的 ID，保持其断言。更新 README/PLAN，三个根规划文件仅跟踪 P4。

## 执行命令与实际结果

根入口均通过原 agentopshub 环境执行；服务测试由 dev.py 分派到各自环境。最终顺序运行记录与日志位于 ignored `.runtime/p4-validation/results.json` 和对应 `.log`。这次无并发 Conda wrapper，无安装或全局环境修改。

```text
conda run -n agentopshub python scripts/dev.py check-contracts
conda run -n agentopshub python scripts/dev.py test-service datacopilot --group governed -q -rs
conda run -n agentopshub python scripts/dev.py test-integration --group delivery
conda run -n agentopshub python scripts/dev.py test-integration --group reports
conda run -n agentopshub python scripts/dev.py test-service harness --group workflow -q
conda run -n agentopshub python scripts/dev.py test-service supportops tests/test_p4_exports.py tests/test_p4_snapshot_postgres.py tests/test_business_data_contract_v1.py tests/test_business_data_export_v1.py tests/test_business_data_delivery_v1.py -o addopts= -q -rs
conda run -n agentopshub python scripts/dev.py test-service supportops --group safety -o addopts= -q -rs
conda run -n agentopshub python scripts/dev.py test-integration --group support
conda run -n agentopshub python -m pytest tests/test_p1_boundaries.py tests/test_p2_boundaries.py tests/test_p3_process_boundaries.py tests/test_p3_integration_cleanup.py -q
conda run -n agentopshub python scripts/verify_import.py
conda run -n agentopshub python scripts/export_p4_contract.py
```

| 验证 | 最终实测 |
|---|---|
| P4 规定四命令 | 全部 exit 0 |
| DataCopilot governed | 279 passed、1 skipped；原 catalog/AST/executor/identity/JWKS/API 与新报表边界 |
| SupportOps producer/Contract/真实 PG | 36 passed、1 skipped；导出/校验/索引失败保留旧索引；真实 concurrent writer 不改变 reader 快照，reader 写入被 PG 拒绝 |
| 真实 HTTP delivery | 6 passed；hash/tenant/schema/path/fingerprint 拒绝、恢复旧索引仍可查询、所有身份错目标服务 token 拒绝 |
| 真实 HTTP reports | 1 passed；19 次真实 workflow run、五个独立 PG oracle、旧目录真实 D1–D4 查询、刷新失败不撤销退款 |
| Harness workflow 回归 | 32 passed；P3 + P4 授权、封闭输入、超时/服务 token、隐私、wire 字段一致 |
| SupportOps safety | 153 passed，0 skipped |
| P3 真实 HTTP 回归 | 1 passed，0 skipped |
| 根 P1/P2/P3 边界 | 23 passed，0 skipped |
| Ruff lint/format | 29 个本次改动 Python 文件通过 E/F/I/UP/B（100 列）和 format；Harness 源码另通过其配置 lint（含文档规则） |
| 类型检查 | Harness 配置 strict mypy 6 个本次源码/导出文件通过；cache 在 ignored `.runtime/mypy-p4`，未宣称全仓类型检查 |
| 保护/契约/diff | 698 源/目标字节及原三仓 HEAD/branch/status 未变；Contract 三份/acceptance 哈希一致；diff 检查通过 |

两项 skip 均为原有测试：consumer 的 real POSIX symlink 需 POSIX runner；producer 的 symlink_creation_not_available 为本机权限限制。未删测试、未计入通过。新增 Windows junction 拒绝测试已实际通过。DataCopilot 的 12 条 warning 为原 LangGraph/Starlette 弃用及 Pydantic Path schema 提示，非新增安全旁路；不安装依赖规避它们。

## AC-07 / AC-08 的真实证据

Reports 测试在独立 schema 导入真实 24 订单（tenant-a 12），通过真实 HTTP 提交一笔低金额退款、准备一笔高金额待审批与人工工单，再刷新。服务消费者没有访问 ORM，oracle 在 SupportOps 自己的进程直接读业务表，以 Python Counter/状态规则计算预期，没有使用报表 SQL 作为 oracle。

| report_id | 刷新后实际 rows = 独立 oracle |
|---|---|
| orders_total | `[[12]]` |
| refund_requests_by_status | `[["pending_approval",1],["submitted",1]]` |
| pending_approval_count | `[[1]]` |
| tickets_by_status | `[["open",2]]` |
| overdue_open_tickets | `[[0]]` |

刷新前退款状态分布为 `[]`，业务提交后未刷新仍为 `[]`；显式刷新后才出现新分布，snapshot_id 改变。旧目录字节保持一致，另一 DataCopilot 进程经过原 consumer/AST/DuckDB 实际查询仍返回旧 `[]`。生成时间、as_of、耗时、snapshot_id、delivery_fingerprint、水位/不可用原因和 template/v1 来源来自真实执行。真实本轮新建工单尚未过 SLA，因此真实 oracle 为 0；另有单测用固定未来时钟验证 overdue 非零计算，模板按 Contract 明确排除 resolved/closed，不声称真实库发生过期。

真实 delivery 的 tenant/schema 负例重算索引指纹后仍被独立 consumer 拒绝；hash 负例破坏数据字节，path/fingerprint 负例由索引解析拒绝。候选 SQL 单测覆盖 DELETE、wildcard、越界表/列、外部 read_csv 和多语句；原 AST/executor suite 同时回归。Closed API 拒绝任意 sql/path/tenant/as_of/policy/question，客户及 tenant-b 无运营权限；错服务凭据真实 HTTP 返回 401。

Reports 的 run 证据在 ignored `.runtime/p4-reports-evidence.json`（19 runs），delivery 在 `.runtime/p4-delivery-evidence.jsonl`（多轮追加）；各 run JSONL 在 `.runtime/runs/`，analytics audit 在本次 `.runtime/demo-<marker>/analytics-audit.jsonl`。断言日志不含正文、SQL、rows、token。测试结果正文保留在直接 HTTP 响应，非 durable run 记录。

## 已修复问题、资源状态与限制

首轮新测试发现 Hive CAST 被解析为原 executor 未允许的 TRY_CAST；改为对 consumer 已类型化 timestamp 字段作服务端固定 UTC 字面值比较，未扩展函数白名单。Python 3.11 缺少 Path.is_junction，改用 Windows reparse-point 属性并实测 junction 拒绝。首轮另有 lint/type 问题，均修复后再跑最终整批；首轮失败不列为成功。

最终只读 SQL：主演示库 24 订单、0 工单、1 pending action、1 action receipt，ORD-1001 requested、ORD-1002 none，随机测试 schema 遗留 0；结果在 `.runtime/p4-validation/database-final.json`。三个自有 HTTP 进程已停止，PostgreSQL/卷和原有业务申请保留。测试 schema 的回收只回收已验证归属的测试资源，不称为业务回滚；ignored 测试快照保留。无原项目源码/DB/秘密修改，无安装/模型下载/计费调用/用户配置修改/commit/push。

索引是本机受控目录的小映射，当前单 worker/线程锁，不是生产签名、分布式发布或归档系统。consumer 每次请求固定目录，已发布目录由应用不再修改；同权限本机用户仍能修改文件，不声称 OS 级不可变。导出失败可能留下未索引的新目录，旧有效索引保持可用；不自动删除这些目录。订单/动作无可靠水位，不声称实时/CDC；Float 精度未迁移。Opaque 演示身份非生产登录，live 模型质量未实测。

P4 验证完成并停止；P5 UI、P6 全套演示/文档验收未执行。

## P4 本地提交授权（2026-10-01）

P4 验证与只读 diff 审阅后，用户明确授权提交 P4、本地保存且不推送。本次提交包含 P4 代码、封闭 API 契约、测试、规划与验证文档；不包含 ignored runtime、数据库、凭据或原项目改动。前文未提交的描述记录验证当时状态；本次提交不进入 P5。审阅补跑 P4 单测 24 passed，真实 HTTP/PG 集成证据为此前已执行的结果，本次审阅未重跑集成。
