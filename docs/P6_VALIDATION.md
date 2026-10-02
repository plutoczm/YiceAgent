# P6 验证与演示版冻结记录

日期：2026-10-02（Asia/Shanghai）。只执行 P6；`v1-demo-ready` 为文档状态，未创建 Git tag。全部规定门禁成功后写入本文与 PLAN/README；没有 commit/push。

## 基线与完成结论

执行前 `git status --short` 和 `git diff --stat` 均为空，HEAD 为 `1fc0d39 feat: add P5 unified demo workbench and regression coverage`。PLAN、P5_VALIDATION 及审阅修复记录均确认 P5 COMPLETE；保留原有六个真实浏览器剧本证据。没有未知未提交工作被覆盖。

最终 `verify --offline` exit 0，`demo-check --all` exit 0。E01–E24 **24 passed / 0 failed / 0 skipped**，AC-01–AC-12 均有 test/run evidence。P6 COMPLETE，停止，剩余主线批次 0。

## 改动与架构

- 新增 demo/scenarios/e2e-v1.json 和 tests/integration/test_demo_v1.py：24 个固定逻辑场景、五报表及 AC 映射；一个自有临时 PostgreSQL schema，真实 HTTP、实际业务持久化和独立 Python oracle。
- 新增 scripts/p6_verify.py、tests/test_p6_verify.py：串行分服务执行，防止 Windows Conda 激活临时文件冲突；逐任务退出码、JUnit、日志、版本指纹、AC 映射；任何失败返回非零，旧源码/旧文档证据拒绝使用，skip 单列。
- 修改 scripts/dev.py、p3_integration.py：verify --offline、demo-check --all，以及复用原隔离 HTTP runner 的 P6/JUnit/log 参数；保持 UI group 的原路径。
- 新增 services/supportops/scripts/p6_database_state.py：由 SupportOps 环境只读核对自有 public 或受检验 test schema 的表行数/指纹，不输出 DSN、秘密或业务正文。
- scripts/demo_process.py 小修复：确认 Windows 进程已退出后返回 None；活进程或无法确认退出状态仍 fail-closed。新增三项 OS API doubles 回归，并通过实际 HTTP 故障后的停机复验。没有削弱 PID/创建时间/可执行路径或资源归属检查。
- 新增 docs/DEMO.md、INTERVIEW.md、本文，更新 README 和 PLAN。SPEC、Business Contract、原服务业务实现与三个原仓库没有更改。

未新增自主 Agent、协议平台、队列、向量库、环境合并或后续阶段。仅调用原领域授权、确认/审批、工单和 AST/DuckDB 管线。expected fixture 用于测试复现比较，未作为产品响应。

## 实际命令与结果

工作目录 D:/Projects/WorkflowOps；Conda 为 D:/Anaconda3/Scripts/conda.exe。最终规定命令实际使用 `conda run --no-capture-output -n agentopshub python -B ...`，业务测试仍由 dev.service_process 分别在 agentopshub/supportops-ai/datacopilot 和对应服务 cwd 执行。

```text
conda run -n agentopshub python scripts/dev.py verify --offline
conda run -n agentopshub python scripts/dev.py demo-check --all
```

verify 内执行 doctor、check-contracts、verify_import；根改动文件 Ruff lint/format、SupportOps 新脚本按原 pyproject.toml 规则 lint/format；必要回归、D5 确定性评测、support/delivery/reports/ui/v1 五组真实 HTTP 验证。全部门禁 exit 0。另执行七个改动 Python 文件 compile 语法检查与最终 Git diff --check。

| gate | passed | failed | skipped |
|---|---:|---:|---:|
| root | 41 | 0 | 0 |
| harness | 291 | 0 | 0 |
| supportops | 219 | 0 | 1 |
| datacopilot | 284 | 0 | 1 |
| http-support | 1 | 0 | 0 |
| http-delivery | 6 | 0 | 0 |
| http-reports | 1 | 0 | 0 |
| http-ui | 1 | 0 | 0 |
| http-v1 | 24 | 0 | 0 |

SupportOps 的 1 skip 为 `symlink_creation_not_available`；DataCopilot 的 1 skip 为 POSIX 专用真实符号链接集成。Windows junction/路径拒绝按既有测试保留；两个 skip 不计 pass，不称为平台外验证。没有删除/改弱原断言。

D5 business_analytics_v1（dataset_version=1.0.0）50/50 case 为 pass，gate_passed=true，tenant_isolation_violations=0、unsafe_query_accepted_count=0。这是既有离线确定性候选/业务分析评测，不是真实模型质量或生产指标。与 golden pytest 两项及 P6 场景有重叠，不重复累加为唯一测试数。E03/E07/E14/E21/E22/E24 内复用回归的参数化用例也不另冒充逻辑场景数。

本轮根脚本/测试和 SupportOps 新脚本没有配置类型检查器；未修改 Harness 的 strict-mypy 覆盖源码，因此未运行无关全量 mypy，未将其写作通过。

## AC 与证据映射

| AC | 逻辑场景 | 结果 |
|---|---|---|
| AC-01 | E01, E24 | PASS |
| AC-02 | E02, E03 | PASS |
| AC-03 | E04, E10, E15, E17, E24 | PASS |
| AC-04 | E05, E06, E16, E23 | PASS |
| AC-05 | E09, E10, E11, E12, E13, E14 | PASS |
| AC-06 | E15 | PASS |
| AC-07 | E17, E18 | PASS |
| AC-08 | E19, E20, E21 | PASS |
| AC-09 | E22, E23 | PASS |
| AC-10 | E07, E24 | PASS |
| AC-11 | E01, E02, E03, E04, E05, E06, E07, E08, E09, E10, E11, E12, E13, E14, E15, E16, E17, E18, E19, E20, E21, E22, E23, E24 | PASS |
| AC-12 | E24 | PASS |

每个 AC 的具体 pytest node、JUnit 路径和实际 run_id 见 `.runtime/p6-f9bce7304606410198209f8ad86710c3/acceptance.json`。总计实际 P6 workflow run 50 个；元数据列表在 e2e-evidence.json，不含正文或凭据。E01/E02/E03 等使用 test evidence，不虚构 run_id。

E14：取消及重放为真实 HTTP；过期/报价漂移/旧审批恢复由既有确定性领域回归验证，未冒充长时间等待的真实浏览器用例。E20 实际 HTTP 测试覆盖五种交付变异及旧快照继续可查询。E21 的 SQL 变异、外部访问和结果预算通过实际 AST/DuckDB 的既有回归，公开 API 另拒绝原始 SQL 输入。E22：停止本轮自有 SupportOps，实际只读请求 failed、写请求 unknown_outcome，独立读取 PG schema 指纹保持不变；一次 dispatch/no retry 使用 transport double 断言，断连不证明业务写入已发生。

AC-03 的浏览器依据仍是 [P5_VALIDATION.md](P5_VALIDATION.md) 在 2026-10-02 的六剧本人工浏览器实测。本轮重跑实际 HTTP UI 绑定和真实 JS 事件处理回归；没有将 DOM doubles 或 HTTP 测试说成重新执行的人工浏览器检查。AC-12 的手册路径与技术取舍已逐项核对实际代码，NOT_RUN 与限制明确。

## 失败历史与修复

首次 `D:\Projects\WorkflowOps\.runtime\p6-3d3ead2959a44e059304009f21b48b59`：E01–E21 passed，E22 在故障后即时重启的 exclusive-bind 检查被拒绝，后续 E23/E24 因依赖失败，共 21 passed/3 failed。改为故障后直接只读核对 PG schema，不重启整套服务；保留实际故障断连和 unknown_outcome 判据。

第二轮 `D:\Projects\WorkflowOps\.runtime\p6-114e38145dd447088a9ba87cd1ea3b79`：23 passed/1 failed，E24 遇到已终止 PID 的残留 Windows handle，QueryFullProcessImageNameW 失败，原 stop 守卫拒绝清理。补上 GetExitCodeProcess 的正面退出证据，状态不可读或活进程不可验证仍拒绝操作。两轮失败保留 summary/JUnit，不写成通过；自有残留 HTTP/schema 已按归属核验后恢复清理，原始证据不删除。

最终通过轮次：`.runtime/p6-f9bce7304606410198209f8ad86710c3`；summary.json、全部 gate log/JUnit、analytics-evaluation.json、e2e-evidence.json、acceptance.json 均保留。最新指针 `.runtime/p6-latest.json`；各轮独立 UUID，不覆盖历史结果。

## 资源保护与限制

public 演示数据库九表的行数和完整行指纹在前后相等：{"action_receipts": 1, "audit_events": 9, "operation_approvals": 0, "orders": 24, "pending_actions": 1, "ticket_events": 0, "ticket_messages": 0, "tickets": 0, "workflowops_demo_owner": 1}。测试 schema 最终 0 个；本轮 HTTP 均停止，8010/8011/8012 可绑定，项目 PG/卷保留。E22 另核对自身隔离 schema 的业务行指纹未变。没有重置主演示库或操作原项目数据库/容器/卷；原库内容未连接审计，保护依据是固定新库边界和未触达原资源，不将其声称为原库逐表审计。

verify_import 确认 698 来源/目标文件哈希一致、原三仓库 HEAD/branch/status 不变。三解释器与模块来源均在新根；真实服务只使用项目自有 PG 和验证后的 DuckDB 快照，没有启动或使用 Redis/向量库，也未停止非自有依赖。

live 模型、模型/配置质量、样本成功失败、usage/cost 均 **NOT_RUN**；未安装依赖、下载模型、修改用户级配置、创建 commit/tag/push。非默认历史模型/RAG/检索评测不列为已执行。BM25 词面局限、Float 金额、非实时快照、demo 身份、单 worker run log、未知写入人工核对及公网/生产能力限制详见 DEMO/INTERVIEW。

`v1-demo-ready` 仅冻结完成状态；P6 到此停止，没有 P7/P8 主线。
