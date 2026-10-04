# Post-P6 Repository Consolidation — 实测记录

日期：2026-10-02（Asia/Shanghai）。本次仅整理仓库，不重做 P1–P6 开发，不新增业务功能。用户选择将目录物理改为 Harness / Support / Analytics；完整原 P6 回归及 acceptance/evaluation 通过后才写本结论。**CONSOLIDATION COMPLETE，停止。**

## 迁移前后目录与运行

| 角色 | 迁移前 | 迁移后 | 原有有效文件 |
|---|---|---|---:|
| Harness | services/harness | services/harness | 231 |
| Support | services/supportops | services/support | 127 |
| Analytics | services/datacopilot | services/analytics | 384 |

全局原有有效文件 802 个均保留：274 个原位字节不变、509 个移位字节不变、19 个文件仅调整文件系统路径、生成物配置位置或当前文档。两个目录内共 511 个有效文件被移位（其中两文件仅改路径字符串）。未从三个原目录重复复制源码：所需有效实现已经完整存在于 WorkflowOps；逐文件哈希验证确认没有丢失有效未提交实现。

只保留 WorkflowOps 根 .git；原始 D:/Projects/AgentOpsHub、supportops-ai、DataCopilot 及各自 .git 均完整保留，不执行 merge/fetch/reset/clean/stash，不修改原环境、数据库、秘密或用户级配置。

原 CLI/内部服务标识 `harness / supportops / datacopilot`、Conda `agentopshub / supportops-ai / datacopilot`、业务 package 名、端口 8010/8011/8012、七个 workflow ID 和 wire/token/session 契约均不变。固定映射见 scripts/service_paths.py；各子进程 cwd/PYTHONPATH、来源边界、Contract/DTO parity 工具和测试路径断言机械调整。没有重构业务逻辑或更换技术栈。

启动仍为根 start.cmd，停止为 stop.cmd，单一 UI http://127.0.0.1:8010。真实 doctor 确认解释器为既有 D:/Anaconda3/envs 下三环境，所有模块来源位于新的三个服务目录。Analytics 现有 logging.file_path 以及服务 pytest cache_dir 通过子进程配置指向根 .runtime，未改变日志级别/业务行为；不合并 Python 环境。

## 保留、增加与排除

- 保留 696 个原项目有效来源文件及两个根 Contract 发布副本，698 个原 source-manifest 检查继续通过；源/目标业务文件哈希仍严格一致。三个 LICENSE、测试、契约、核心源码、数据 fixture、必要文档/依赖声明均保留。
- Support 原仓库未跟踪的 business_data、Contract、export/validate 脚本及测试均已保留；原 README/CI 未提交修改也保留原字节。Analytics 的原未跟踪规划记录及各服务其他历史规划记录保留为来源。共 24 个原有效未跟踪文件保留，含历史规划记录，未将生成 egg-info 当实现。
- 新增三个 role README_MONOREPO.md、一个 service-map.json、一份固定路径 helper、当前 MONOREPO 指南及基线/逐文件/排除/对比/最终状态证据。三份原 .env.example 经检查后以 config/upstream.env.example 参考模板保留，密钥为空/占位，offline 不自动加载，不复制真实 .env。
- 原输入中 39,198 条排除/ignored metadata 涉及环境、缓存、模型/构建、日志、秘密等，未复制这些内容。完整本地清单在 ignored .runtime/consolidation/baseline-complete.json，精简可审阅类别/路径记录见 exclusions.json。
- 目标服务目录已有 55 个 ignored 缓存/日志移入 .runtime/consolidation/quarantine；补充 lint 产生的 7 个缓存另移入 quarantine-after。仅移除已核验在目标内的空生成目录；不删除日志证据、不读取日志正文、不操作任何原目录。验证后 services 内没有 ignored 文件；将来新缓存/日志由根配置集中生成。
- 原 README/独立 Compose/非默认前端、训练与检索资产保留为历史参考，不冒充当前 Monorepo 必需或已验证路径。不迁移旧 .git、Conda、权重、数据库、真实配置或构建运行产物。

每个原有文件的新旧路径、before/after SHA-256、来源及分类见 [file-manifest.json](consolidation/file-manifest.json)。Git status 与有效未提交清单见 [baseline.json](consolidation/baseline.json)；排除/隔离见 [exclusions.json](consolidation/exclusions.json)。历史 source-manifest.json、P1–P6_VALIDATION 均保持原字节，当前指南解释旧路径，verify_import 通过固定映射检验新路径，未重写历史哈希或放松断言。

## 原 P6 全量回归与前后对比

工作目录 D:/Projects/WorkflowOps。实际入口为既有 D:/Anaconda3/Scripts/conda.exe run --no-capture-output -n agentopshub python -B；服务测试继续由原对应 Conda 环境执行：

```text
conda run -n agentopshub python scripts/dev.py verify --offline
conda run -n agentopshub python scripts/dev.py demo-check --all
```

两命令 exit 0。复用原完整单元/集成/E2E/评测集合及真实 PostgreSQL/HTTP/AST/DuckDB，没有删减、skip 新用例或降低断言；只有文件路径及生成物配置调整，补充路径日志断言。原 P6 的 17 个 gate 退出码、pass/fail/skip 和 skip 原因均与基线一致。

| gate | 原 P6 passed | 整合后 passed | 整合后 skip |
|---|---:|---:|---:|
| root | 41 | 41 | 0 |
| harness | 291 | 291 | 0 |
| supportops | 219 | 219 | 1 |
| datacopilot | 284 | 284 | 1 |
| http-support | 1 | 1 | 0 |
| http-delivery | 6 | 6 | 0 |
| http-reports | 1 | 1 | 0 |
| http-ui | 1 | 1 | 0 |
| http-v1 | 24 | 24 | 0 |

E01–E24 24/24，0 failed/skip；AC-01–AC-12 全部通过。D5 business_analytics_v1 50/50，其 case_results 与 metrics 与原 P6 完全一致；不将确定性评测当作真实模型指标，也不将重复/嵌套回归相加为唯一用例数。Support 1 skip 是 Windows 无符号链接权限，Analytics 1 skip 是 POSIX 专用符号链接；均与原 P6 相同，不计 pass。

E2E 的 24 个逻辑场景、每场景的 workflow 终态顺序和 domain_refs 形状逐项相等，两轮均 50 个实际 workflow run。五报表在两轮中均对同一独立 PG oracle 检查实际 rows/count；fixture/SQL/授权不变量不变。UUID、时间戳/as_of、耗时、snapshot/export 指纹和进程/session handle 本来可变，不按字节要求相等。归一化对比见 [regression-comparison.json](consolidation/regression-comparison.json)。

P6 基线：.runtime/p6-f9bce7304606410198209f8ad86710c3；整合后：.runtime/p6-b828732e207e4a5a9e08d3e7e2bce07d。JUnit、gate log、50 场景评测、E2E run evidence 和 AC 映射均保留，各轮 UUID 目录不覆盖。

## 安全保护、Git 与剩余限制

三个原项目的 HEAD/branch/raw porcelain 状态及 696 个有效来源文件哈希前后完全相同。WorkflowOps 原有 P6 13 项未提交文件保留；旧路径的删除/新路径的未跟踪显示是未暂存目录移动，实际文件由上述哈希清单证实保留，没有 commit/tag/push。根 HEAD 仍 1fc0d3952440b20892a5e36ce6a3678eb5bd8f66、branch main，staged diff 为空。完整最终 Git status 见 [final-state.json](consolidation/final-state.json)，尚未创建本地提交。

public 九表的完整行指纹等于原 P6 baseline、整合前及整合后，测试 schema 0，自有 HTTP 已停止、端口可绑定，项目 PG/卷保留。没有重置主库，不读取原项目真实库；原库保护依据是未触达与既有固定新库边界，不宣称对原库做了逐表审计。无嵌套 .git、环境副本、真实密钥或数据库源文件。

必要 root/path lint 与原 P6 format gates通过；受影响 Harness/Support 测试按各自正确 cwd 检查、Analytics 按原有效规则检查均通过。早期额外 lint 用 root cwd 解析服务配置产生相对 src/per-file 差异；正确作用域重跑通过，未借机改动未变化的 P6 状态脚本或 Analytics 的既有安全错误处理。一次 PowerShell inline 引号解析和一次 GBK 读取失败均在只读辅助检查，改为保存的 UTF-8 Python 脚本；metadata-only 模型目录过滤/porcelain 空白/formatter 比较差异已修正，不影响文件内容与最终门禁，记录保留在 planning/progress 与本地 audit。

未更改配置或执行无关全量 type checker；typed 业务源码未变，未宣称全部历史代码 mypy 通过。浏览器人工作业证据沿用 P5 六剧本，原 P6 HTTP/JS/E2E 门禁完整重跑，未把它说成新人工浏览器验证。live 模型/费用/质量、生产登录、公网部署、高并发、真实支付和非默认历史模块完整运行仍 NOT_RUN。

整合完成后停止，没有 P7/P8 或新业务需求。
