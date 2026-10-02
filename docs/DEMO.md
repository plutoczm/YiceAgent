# WorkflowOps 演示手册

默认 offline；业务数据全部为固定 seed=42 的模拟数据。身份选择是本地演示器，不是真实登录。退款申请提交不表示支付完成。仅绑定 127.0.0.1。

## 前置检查与唯一入口

在 D:\Projects\WorkflowOps 使用既有 agentopshub、supportops-ai、datacopilot Conda 环境。Docker 中项目拥有的 PostgreSQL 应健康；无需 Redis、向量数据库、模型或前端构建。不要修改原项目环境或数据库。

```text
conda run -n agentopshub python scripts/dev.py doctor
conda run -n agentopshub python scripts/dev.py check-contracts
start.cmd
```

浏览器打开 http://127.0.0.1:8010。演示结束执行 `stop.cmd`；只停止核验身份的三个 HTTP 进程，保留 PostgreSQL、卷、模拟业务和历史快照。端口占用时启动失败，不自动终止占用者，不安装依赖。

尚未准备数据时，使用 `conda run -n agentopshub python scripts/dev.py seed-demo --seed 42`。相同 fixture 为 no-op，不清空已有业务。需要重新演示全新退款时，可在明确愿意清空本项目演示业务后手动使用 `--reset-demo`；此命令具有破坏性，不是普通启动/验证步骤。不要将运行配置、密码和 token 提交 Git。

## 六个演示剧本

1. **准备数据**：doctor 核对三环境、来源与端口，seed-demo 显示实际 24 订单和 fixture hash。tenant-a 有 12 个订单。重复导入不会累加。P6 自动验收用独立 schema，不改变主演示库。
2. **客户查询和证据**：选 customer-a1，查询 ORD-1001，金额为 199。ORD-1005 是另一客户，ORD-2001 是另一租户，均拒绝。咨询 `refund policy` 应显示引用；`zzzzqxyunrelated` 无充分证据，转人工。offline 输出是检索摘录，不是模型回答。
3. **退款与审批**：低金额 ORD-1001 先申请，显示 waiting_confirmation；明确确认后为 submitted/退款申请已提交。再次确认不重复提交。高金额 ORD-1002 为 waiting_approval；切换 operator-a，读取实际版本并审批，仍需客户再次确认。客户不能自己审批。若主库已演示过订单，页面会展示真实已有状态，不伪造初始状态。
4. **人工接管**：客户创建人工工单，记录实际 ticket_id；operator-a 分派并输入回复；再切回客户查看与继续回复。对话持久化在 SupportOps。业务已确认但运行记录失败时单独告警；写入未知时不要重发，先核对关联 operation_id。
5. **快照分析**：operator-a 在数据视图显式刷新，再查看五种模板报表。创建新工单或完成新退款后，刷新前仍是旧统计；刷新后变更才进入分析。结果显示 template/v1、实际 rows/row_count、snapshot_id、生成时间、水位、as_of 和耗时。快照不是实时数仓。
6. **失败和边界**：用其他客户身份读取订单/已知 run，观察拒绝。坏快照及下游故障使用下面隔离验证入口复现，避免手动损坏主演示快照或停止他人服务。运行页显示失败步骤和 unknown_outcome；不能把未知写入当作确定失败或自动重放。

切换身份会清空 UI 上下文，但服务端始终检查权限。最近运行列表属于当前页面会话；已知 run_id 仍可按后端 ACL 查看持久元数据。页面不提供任意 SQL、路径、租户或角色权限输入。

## 完整验证与证据

先 `stop.cmd`，再执行：

```text
conda run -n agentopshub python scripts/dev.py verify --offline
conda run -n agentopshub python scripts/dev.py demo-check --all
```

verify 检查必要服务回归、独立 PG schema 的真实 HTTP 集成和 E01–E24。demo-check 对本次通过的 JUnit/run evidence 生成 AC-01–AC-12 映射；源码、测试或本手册变动后旧报告被拒绝，须重跑 verify。两命令均以非零退出码报告失败；skip 单列，不算 pass。

每轮证据在 ignored `.runtime/p6-<UUID>/`；`.runtime/p6-latest.json` 仅指向最新轮次，历史报告不覆盖。summary.json、各回归 JUnit、e2e-evidence.json 和 acceptance.json 可核对结果及 run_id。浏览器六剧本实测见 P5_VALIDATION；P6 HTTP/JS 回归与 P5 人工浏览器证据分开记录。实际最终结果见 [P6_VALIDATION.md](P6_VALIDATION.md)。

## 模型和已知限制

工作台当前只有 offline。原服务的可选模型适配器保留，真实模型调用为 **NOT_RUN**：没有模型可用性、质量、usage 或费用证据。不自动读取其他项目密钥、不下载 embedding、不静默冒充模型成功。

BM25 对词面重合有效，语义改写召回有限；无证据就拒答/转人工。源业务金额仍为 Float，整数元演示和 decimal 字符串交付不等于精确资金账本。run JSONL 是单 worker 的元数据记录，不是恢复引擎；没有分布式 exactly-once、生产登录、公网部署、高并发或生产 SLO 验收。未知写入需要人工核对。旧快照保留，没有自动归档清理策略。

Post-P6 仓库整合：物理服务目录已整理为 `services/harness`、`services/support`、`services/analytics`；原 CLI 标识和 Conda 环境保持兼容。当前运行与历史路径说明见 [Monorepo 指南](MONOREPO.md)；本次仅仓库整合，P1–P6 开发状态不变。
