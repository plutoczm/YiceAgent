# V2-2 双场景业务验收

阶段：COMPLETE，仅 V2-2。实施开始于 2026-10-03；验收完成于 2026-10-04（Asia/Shanghai）。依据 [SPEC_V2](SPEC_V2.md)、[PLAN_V2](PLAN_V2.md)。V2-3–V2-5 尚未开始，v1 的 P1–P6 记录保持原样。

## 实现与业务边界

| 实现 | 文件与职责 |
|---|---|
| 封闭请求与精确金额 | `v2/src/workflowops/business/contracts.py`、`rules.py`；拒绝输入租户、权限、价格和数据库字段；金额为 Decimal/Numeric(12,2) |
| 数据与事务 | `models.py`、`service.py`；订单、支付、退款分开；资源锁先于动作锁、版本检查、草案快照、独立审批、唯一回执、按 actor 隔离的请求幂等 |
| 初始化与剧本 | `bootstrap.py`、`scenarios.py`、`demo/fixtures/v2`；只允许 owned v2 库及自建测试 schema，固定 seed 42，拒绝未知表与版本，完成标记控制启动 |
| HTTP 与身份 | `identity.py`、`business/api.py`、`app.py`、`server.py`；回环地址、同源校验、固定演示身份、HttpOnly cookie、CSRF、隐去原始校验输入 |
| 控制台 | `v2/src/workflowops/static`；商品、订单、模拟事件、动作审批确认、工单；切换身份清空旧视图，未知结果锁定写入并允许只读核对 |
| 启动与公开契约 | `scripts/v2.py` 的 business-prepare/replay-demo；`contracts/service_api/v2/business.openapi.json`，17 个路径；请求类型已冻结，响应尚以公开字段字典表达 |
| 测试 | `v2/tests/test_business_rules.py`、`test_business_postgres.py`、`test_business_http.py`、`test_business_provision.py`；保留基础阶段回归 |

这些规则是自编演示规则，不代表真实电商或外卖平台。没有实际支付、到账或外部业务写入。

电商履约为待支付→已支付→待发货→已发货→已送达，发货前可取消；外卖为待支付→已支付→已接单→制作中→配送中→已送达，商家接单前可取消。支付或退款申请经客户确认后只形成 pending/requested，由运营模拟成功/失败事件决定资金结果；履约也由运营手动推进。

所有下单、支付、取消、退款、工单创建/回复/分派/状态变更先草案后确认。电商金额严格超过 500 元、外卖严格超过 100 元的退款或已支付取消，先运营审批，再由客户确认。草案 15 分钟过期；订单、商品或规则版本变化使旧草案失效。审批绑定草案快照哈希。

已支付取消以同一事务更新取消和整单退款申请；普通整单退款只改变退款状态，不自动撤销配送。只允许退实际已支付的整单金额。明确模拟失败允许新的草案与确认；结果未知时不自动重放。操作提交前失败回滚；提交成功而回执丢失返回 `unknown_outcome`，可以按动作 ID 或当前 actor 的客户端请求 ID 查询。

长期事实仍在 PostgreSQL；页面显示真实结构化字段。业务审计只记录元数据，工单内容在有权限的业务查询中读取。固定演示身份便于验证隔离，不是生产登录系统。

## 执行结果

| 检查 | 实际结果 |
|---|---|
| 完整 v2 pytest（真实集成开关开启） | **83 passed，0 failed，0 error，0 skipped**；40.346 秒 |
| 严格 mypy（已配置范围） | PASS，11 个源码；contracts/config/rpc/device/identity 与 6 个业务核心源码；不宣称整个 HTTP/UI 都经过严格类型检查 |
| Ruff lint、Python 格式检查 | PASS |
| Node `--check` 静态 JS | PASS |
| 真实 PostgreSQL | 两类生命周期、审批、越权、请求冲突、并发幂等、取消/退款争用、漂移、失效、回滚、提交后未知结果核对 PASS |
| 真实 HTTP | 独立回环 uvicorn 服务实际调用；两类支付/取消/退款与工单、cookie/CSRF/身份切换、未知结果核对 PASS |
| 独立剧本 | 两类送达后整单退款及高额审批后取消退款，共 4 个场景；隔离测试重放两次；主 v2 库真实执行一次 |
| 业务初始化 | 主 v2 库初次 seed 完成，第二次 no_op；16 商品、24 个初始订单；通过业务服务推进生命周期 |
| 浏览器 | 实际点击下单、客户确认、模拟支付、高额退款运营审批、客户确认、模拟退款成功、工单创建/分派/回复；身份 B 不显示 A 的业务；重启后结果仍可查询 |
| 测试资源 | 本轮 UUID 测试 schema 已清理；最终剩余 0 个 `v2_test_*` |
| 来源/环境保护 | 原 830 文件哈希一致；agentopshub/supportops-ai/datacopilot 包清单分别 76/79/166 项一致；v1 10 张表摘要一致 |

最后完整测试记录：`.runtime/v2/p2-tests.xml`；开始时间 2026-10-04T00:45:59+08:00。验证入口（从项目根目录，用新 main 环境）：

```powershell
$env:WORKFLOWOPS_V2_INTEGRATION = "1"
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B -m pytest -c v2/pyproject.toml v2/tests --junitxml=.runtime/v2/p2-tests.xml
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B -m ruff check --config v2/pyproject.toml v2/src v2/tests scripts/v2.py
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B -m ruff format --config v2/pyproject.toml --check v2/src v2/tests scripts/v2.py
```

mypy 的配置路径按 `v2` 工作目录解析，应从该目录执行：

```powershell
& "..\.runtime\v2\envs\main\Scripts\python.exe" -B -m mypy --config-file pyproject.toml
```

详见 `v2/README.md`。不改用户级环境变量。真实集成测试只在 owned v2 库中新建并清理本轮 UUID schema，不重置 public 数据。

## 可核对的实际业务证据

固定 fixture hash：
`53c3d2769aebe2594440b978f8aed9acc37fc03fc62fd61c2772b2a1a4611627`。

主库剧本 ID：`4aaad997-9690-42e9-b095-c9d32f4f043a`。四个场景结果在 `.runtime/v2/business-replay-4aaad997-9690-42e9-b095-c9d32f4f043a.json`。

浏览器订单：`ECO-3C4FDD98D3DF46BC94A93092051E910E`，实际模拟支付及全额退款均 899.00 元。退款动作 `ACT-0AA0F4D29FFC49ECA1D3E02EDE1A427E`，实际运营审批后客户确认。工单 `TKT-663C01FEBC3F4E68858164CD3307BC39` 已分派运营且有两条对话。

重启后浏览器截图：`.runtime/v2/browser-p2/ticket-after-restart.png`。截图是浏览器执行记录的补充，不能替代真实操作。

最终 owned public 数据：29 订单（24 seed + 4 剧本 + 1 浏览器）、16 商品、38 动作和 38 回执、3 审批、1 工单/2 消息、69 模拟事件、148 命令回执/元数据审计。保留演示结果，不自动删除。

机器记录见 `.runtime/v2/p2-evidence.json`、`p2-preservation-evidence.json` 与 `p2-checks.json`。这些 runtime 文件被 Git 忽略，不包含真实账号凭据。

## 修复与限制

实施中发现并修复：命令列表遗漏新子命令；工单“查看对话”后通用刷新清空对话；金额表示接受非有限值或多余小数。修复后完成上述测试。最后复查曾从根目录误用 v1 Ruff 配置（17 个规则错误），未改代码，显式指定 v2 配置后 lint/格式检查通过。浏览器服务重启导致旧连接失效，重新连接并验证持久化结果。失败不作为通过证据。

本阶段只完成确定性业务和最小控制台，未运行 LLM、Pi 账号登录、DeepSeek、Ollama 对话、真实 RAG 入库或记忆/压缩；这些统一记为 NOT_RUN/NOT_STARTED。没有安装新依赖、读取旧凭据、计费调用、提交或推送。GPU 优先配置保留，本阶段业务不需要 GPU。

HTTP 当前运行于 `http://127.0.0.1:8020`，PostgreSQL/Qdrant 保留。启动/停止和手动演示见 [DEMO_V2](DEMO_V2.md)。V2-2 完成不代表 v2 整体完成。
