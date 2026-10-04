# Post-P6 Monorepo 运行与维护

当前来源退役说明：用户选择自生成数据并授权删除原三个目录及私有历史备份。正常来源验证已改为核对 Monorepo 文件与冻结 manifest 哈希，不再读取原目录；历史审计仅通过显式 --verify-originals 执行。下文的原目录保全描述属于整合时记录，当前退役范围与状态见 [REPOSITORY_RETIREMENT.md](REPOSITORY_RETIREMENT.md)。

WorkflowOps 的 P1–P6 开发及验收已完成；本次是仓库整理，未新增开发阶段或业务功能。用户选择物理目录 Harness / Support / Analytics，并保持三个既有 Conda 环境和内部/CLI 标识兼容。

## 目录和职责

```text
WorkflowOps/                  # 唯一根 .git
  start.cmd / stop.cmd        # 统一 offline 入口
  scripts/                   # 子进程分派、路径映射、原 P6 验证
  services/
    service-map.json         # 人可读角色/环境/端口清单，非权限或动态路由配置
    harness/                 # AgentOpsHub；app 位于 backend/src
    support/                 # SupportOps；app、事务、审批、工单、producer
    analytics/               # DataCopilot；backend/app、consumer、AST/DuckDB
  contracts/                 # 原 Business Contract、fixture 和 wire 契约
  demo/                      # 原固定模拟输入/期望和 E01–E24
  tests/                     # 原跨服务黑盒测试；服务测试仍在服务内
  infra/compose.demo.yml     # 仅项目自有 PostgreSQL
  docs/                      # Spec、完成的 Plan、历史验收、当前指南与整合证据
  .runtime/                  # ignored；配置、凭据、报告、快照、运行日志、测试缓存
  .planning/post-p6-consolidation/  # 本次行政任务的计划与过程记录
```

| 角色 | 物理目录 | 原 CLI/服务标识 | Conda | PYTHONPATH | 端口 |
|---|---|---|---|---|---:|
| Harness | services/harness | harness | agentopshub | backend/src | 8010 |
| Support | services/support | supportops | supportops-ai | . | 8011 |
| Analytics | services/analytics | datacopilot | datacopilot | . | 8012 |

根 `scripts/service_paths.py` 是身份到物理目录的固定映射，改变的只有文件位置。端口、session/token 映射、HTTP/workflow ID、业务 DTO、订单/动作状态机、AST 规则和 SQL 模板均不变。两个 app 命名空间继续分进程，不从根 pytest 跨服务 import。

## 使用现有环境运行

在 D:/Projects/WorkflowOps 执行：

```text
conda run -n agentopshub python scripts/dev.py doctor
start.cmd
# 浏览器 http://127.0.0.1:8010
stop.cmd
```

数据和 PostgreSQL 沿用已验收的自有资源，本次不重做 seed/reset/import 开发流程，不重置主演示库。root scripts/dev.py 仍使用原 Conda 的解释器，每个子进程只导入新 Monorepo 对应目录；没有修改用户 PATH、环境或原项目 editable install。

Analytics 的既有 logging.file_path 通过子进程环境指向根 .runtime；服务 pytest cache_dir 同样指向根 .runtime/pytest-cache。这是生成物位置调整，未改变记录级别、业务行为或模型模式。无用缓存/日志不作为源码复制，已有生成物保留在 .runtime/consolidation/quarantine，不读取日志正文、不删除原资源。

## 原 P6 回归入口

先停止工作台，然后原样执行已有完整门禁：

```text
conda run -n agentopshub python scripts/dev.py verify --offline
conda run -n agentopshub python scripts/dev.py demo-check --all
```

复用原单元测试、隔离真实 PostgreSQL/HTTP 集成、E01–E24、五报表独立 oracle、D5 离线业务评测和 AC-01–AC-12。只机械更新路径断言与入口，不减少测试、放松断言或以 mock 替换真实链路。各环境仍独立，失败/skip 保留，不计 skip 为 pass。

## 来源和历史证据

- [基线](consolidation/baseline.json)：四个 Git 工作树、有效未提交实现、P6 已通过的 baseline。
- [文件清单](consolidation/file-manifest.json)：每个已存在文件的新旧路径、前后哈希、来源、保留/移动/路径调整分类。
- [排除清单](consolidation/exclusions.json)：环境/缓存/构建/日志/密钥的排除类别、已审核参考模板和 target 生成物隔离记录；完整扩展 metadata 在本地 ignored .runtime 中。
- [整合验证记录](CONSOLIDATION.md)：全部门禁通过后记录实际结果、行为对比和最终 Git/资源状态。

三个原目录保留，原 .git、未提交实现、Conda 环境、业务库和秘密都不迁移或修改。原 source-manifest.json 及 P1–P6_VALIDATION 是历史证据，保留原字节；其中 services/supportops / services/datacopilot 指当时路径。verify_import 通过固定映射检查现路径，继续要求 698 来源文件字节一致和原 Git 状态不变，不重写历史哈希或降低断言。

当前演示步骤见 DEMO.md，面试解释见 INTERVIEW.md。各服务 README_MONOREPO.md 是当前入口，原 README/Compose/其他非默认前端与训练资产仅保留来源，不视为已验证的统一运行方式。

## 边界

只有离线模拟数据闭环。原模型适配器保留，live 模型、费用/真实质量、生产登录、公网部署、高并发、真实支付及完整非默认历史模块回归仍 NOT_RUN。已知两个符号链接平台 skip 仍单列。run log 非恢复引擎，分析显式刷新，不声称实时或生产级 SLO。整合不创建 commit/tag/push，也不追加 P7/P8。
