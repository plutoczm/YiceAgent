# P1 安全导入与运行边界：验证记录

日期：2026-10-01（Asia/Shanghai）。范围仅 P1；P2–P6 未执行。

## 结论与边界

P1 Definition of Done 满足：工作树源码按字节导入；三服务进程/环境隔离；导入来源在新目录内；原仓库 HEAD、branch、porcelain 状态及导入源文件哈希未变；根/producer/consumer Contract 与 acceptance 一致。
这仅证明 AC-01 的导入/环境部分及 AC-10 的环境部分，不代表服务启动、数据库迁移或离线业务闭环通过。
没有安装/升级依赖、合并环境、创建模拟数据、实现 Workflow/UI/RAG/MCP、启动容器、下载模型、commit 或 push。

原 P1 实测时 WorkflowOps 根 `.git` 未初始化，服务内无嵌套 `.git`；符合 P1 允许明确列为待完成的分支。
原 P1 实测时 Git 实际根为 `D:/Projects`，分支 main、No commits yet；`git status --short --branch -- .` 为 `?? ./`。
父仓库及其其他未跟踪项目不做处理。2026-10-01 用户随后明确授权初始化独立根 Git、检查暂存内容并提交 P1 基线；仓库已在 D:/Projects/WorkflowOps 初始化，不合并历史、不 push。父仓库未操作。

## 新增/修改文件

- 新增 scripts/import_sources.py、dev.py、check_contracts.py、verify_import.py。
- 新增 tests/test_p1_boundaries.py、pytest.ini、.gitignore、infra/compose.demo.yml。
- 新增 docs/source-manifest.json、本文；修改 README.md 与 PLAN.md 的 P1 状态/证据。
- 按 manifest 新建 services/harness（209 文件）、services/supportops（112 文件）、services/datacopilot（375 文件），以及根 contracts/business_data/v1 的 2 文件。
- `.runtime/` 保存 environment-audit.json、import-audit.json、validation-results.json、命令日志及本批临时 Python 辅助脚本；不纳入版本控制。
- 既有 AGENTS.md、SPEC.md、SOURCE_AUDIT.md 保持原样；服务业务代码和 LICENSE 未编辑。

## 最终目录

```text
WorkflowOps/
  AGENTS.md
  README.md
  .gitignore
  pytest.ini                  # 根 pytest 只收集 tests/
  docs/{SPEC.md,PLAN.md,SOURCE_AUDIT.md,P1_VALIDATION.md,source-manifest.json}
  services/
    harness/       # 原 backend/src/app、backend/tests、迁移、scripts、docs 等
    supportops/    # 原 app、contracts、migrations、tests、静态资产等
    datacopilot/   # 原 backend/app、contracts、tests、frontend、web 等
  contracts/business_data/v1/{contract.json,compatibility_baseline.json}
  scripts/{import_sources.py,dev.py,check_contracts.py,verify_import.py}
  tests/test_p1_boundaries.py
  infra/compose.demo.yml
  .runtime/        # ignored
```

保留的源项目 frontend/web/RAG/MCP/training 文件是历史源码快照，不是本次新增功能。
各服务历史 README/Plan/工作记录只描述原项目语境；本项目后续阶段由根 docs/PLAN.md 决定。

## 环境与导入来源

| 服务 | cwd | Conda | 解释器/Python | 子进程 PYTHONPATH |
|---|---|---|---|---|
| Harness | services/harness | agentopshub | D:/Anaconda3/envs/agentopshub/python.exe / 3.12.14 | services/harness/backend/src |
| SupportOps | services/supportops | supportops-ai | D:/Anaconda3/envs/supportops-ai/python.exe / 3.11.15 | services/supportops |
| DataCopilot | services/datacopilot | datacopilot | D:/Anaconda3/envs/datacopilot/python.exe / 3.11.15 | services/datacopilot |

doctor 实际导入顶层 app/app/backend.app 及代表性 agents.models/business_data.protocol/contract_catalog；各 `__file__` 均位于对应服务快照。必需模块实际导入成功，无缺失。替换继承的 PYTHONPATH，禁用用户 site 与子进程 bytecode 写入；不重绑源项目 editable install。
四个计划端口 127.0.0.1:8010/8011/8012/55432 可绑定。Docker CLI/daemon 29.6.1、Compose v5.3.0 可用。
environment-audit.json 只存解释器、来源、版本、模块缺失列表及端口/Docker 检查结果，不保存环境变量或凭据。

Compose project 固定为 workflowops-demo，镜像固定 postgres:16.13-trixie，数据库/用户 workflowops_demo，卷由该 project 命名并含 owner label，只绑定 localhost:55432。
选择 PostgreSQL 16 的具体补丁/发行版标签避免 latest 漂移；标签依据 [Docker 官方镜像 Tags](https://hub.docker.com/_/postgres/tags?name=16&page=1)。尚未拉取镜像或启动数据库，密码必须由进程本地配置提供，不落源码。
P2 创建资源时仍须核验数据库/卷归属，拒绝接用未知既有资源。

## 执行命令与结果

以下均在 D:/Projects/WorkflowOps；conda 实际使用 D:/Anaconda3/Scripts/conda.exe。

```text
conda env list --json
conda run -n agentopshub python scripts/import_sources.py --dry-run
conda run -n agentopshub python scripts/import_sources.py --apply
conda run -n agentopshub python scripts/dev.py doctor
conda run -n agentopshub python scripts/dev.py check-contracts
conda run -n agentopshub python -B -m unittest discover -s tests -p test_p1_boundaries.py -v
conda run -n agentopshub python -B -m pytest -q
conda run -n agentopshub python scripts/dev.py test-service harness backend/tests/agents/test_runtime.py backend/tests/llm/test_contracts.py
conda run -n agentopshub python scripts/dev.py test-service supportops tests/test_business_data_contract_v1.py tests/test_business_data_delivery_v1.py tests/test_business_data_export_v1.py -rs
conda run -n agentopshub python scripts/dev.py test-service datacopilot tests/infrastructure/business_data/test_contract_catalog.py tests/infrastructure/business_data/test_architecture.py
conda run -n agentopshub python -B scripts/verify_import.py
conda run -n agentopshub python -m ruff check scripts tests/test_p1_boundaries.py
conda run -n agentopshub python -m ruff format --check scripts tests/test_p1_boundaries.py
conda run -n agentopshub docker compose -f infra/compose.demo.yml config --quiet
```

源库逐个执行 `git rev-parse HEAD`、`git branch --show-current`、`git status --porcelain=v1 --untracked-files=all`，并检查关键目录/依赖配置/资源与 Contract 路径。没有 fetch。

| 检查 | 最终结果 |
|---|---|
| dry-run / apply | exit 0；698 文件，37,060 排除路径，0 冲突；目标存在且同内容时 no-op |
| doctor | exit 0；三解释器/包来源/必需模块、四端口与 Docker 均通过 |
| check-contracts | exit 0；三份 contract 与 acceptance SHA-256 一致 |
| 新增边界单测 | 8 passed，0 skipped：拒绝覆盖/源漂移/路径越界/reparse，环境隔离，Contract 漂移，清单 JSON 与重复 apply |
| 根 pytest 发现边界 | 8 passed，仅根 tests/；不收集三服务源码 |
| Harness 选定回归 | 101 passed |
| SupportOps 选定回归 | 31 passed，1 skipped（symlink_creation_not_available）；跳过不计为通过 |
| DataCopilot 选定回归 | 37 passed |
| verify-import | exit 0；698 个源/目标 SHA-256 全一致；三源 Git 状态不变；无嵌套 Git/导入 DB/私钥/真实 .env |
| ruff check / format --check | exit 0 |
| Compose config --quiet | exit 0；进程内随机临时值仅用于解析必填密码字段，不保存、不连接数据库 |

Contract SHA-256：`7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b`。
根新增脚本没有配置独立类型检查器；三服务原有配置保留，未对未修改业务源码重复运行全量类型检查/全量测试。P1 未规定启动数据库的集成测试；实际进程分派、导入来源与契约加载已通过 doctor 及选定搬迁回归验证。

## 与历史审计及执行假设的差异

- 三源仓库 HEAD/branch/status 与 SOURCE_AUDIT 一致；前后原文件哈希及状态保持一致。
- SOURCE_AUDIT 的“目标目录此前不存在”是 2026-09-30 历史检查；本次基线目标已含 AGENTS.md、README.md、三份 docs，无服务源码或自己的 .git。
- 裸 python 在当前 shell 无法正常执行；使用现有 Conda/绝对解释器，未修改 PATH/Conda。
- Windows Conda 多行 `-c` 参数拒绝及并发临时文件冲突曾导致失败；探测改为单行，最终批次串行执行。
- 初版排除规则误排 Harness ORM models；已限定顶层模型目录并新增回归断言，补入原字节源码。
- 初版清单写入局部变量覆盖导致清单不是 JSON；已修复并新增真实文件/清单/idempotent apply 单测。旧生成清单仅归档 .runtime，未覆盖用户既有文件。
- 最终所有 P1 规定命令及新增测试通过后才更新 PLAN；失败与 skip 没有写为通过。

## 原项目未提交工作保留

AgentOpsHub：feat/phase7a-knowledge-agent，HEAD 5daedb6307f1eea7bd0e3191fcbf02eb3e8d0f97，ahead 1；工作树无短状态条目。
SupportOps：main，HEAD 90ba9a7ba9f0fd71c4267de6b91f3a68587a32a3，ahead 3；保留修改后的 .github/workflows/ci.yml、README.md。
未跟踪 app/business_data 的 7 个 Python 文件、contracts/business_data/v1 两份 JSON、三个 BUSINESS_DATA 文档、两个 scripts、三个单测及 integration/test_business_data_snapshot_postgres.py 全部原字节导入。
SupportOps 和 DataCopilot 的 findings.md/progress.md/task_plan.md 工作记录亦导入。
DataCopilot：feat/governed-business-analytics，HEAD facc6c31eeb902a80ea1d5521f12459f84f124bf，工作记录仍未跟踪。
supportops_ai.egg-info 不导入，但原生成物完整保留。三原目录、数据库、环境和密钥未清理或修改。
source-manifest.json 记录全部导入路径/tracked 标志/HEAD/哈希和排除路径/原因，是完整文件级证据。

## 限制与 P2 前事项

没有 P1 实现阻塞。独立根 Git 已按后续授权初始化，原父仓库未操作。本次仅提交 P1 基线；P2 仍未开始。
真实符号链接消费者负例在当前 Windows 被跳过；P1 的 reparse 拒绝已有单元测试，但不声称真实文件系统链接测试通过。
真实 .env、.env.example 均未导入；历史服务 bootstrap 测试若依赖原 .env.example 不属于本次通过的回归集合。历史模板在原仓库完整保留，后续需要时应审阅模板后显式纳入，不能复制真实秘密。
源 DataCopilot 重模型/记忆组件未加载；demo-only lazy composition 属于后续阶段。旧启动脚本仅归档保留，不能用其启动/接用原数据库。
原项目 PostgreSQL 集成、三套全量回归、24 场景与完整 AC-01/AC-10 未验证；依 Plan 在对应阶段执行。
到此停止。P2–P6 的实现与状态均未开始。

## P1 本地 Git 基线（后续明确授权）

2026-10-01 在本目录执行 git init -b main；仓库根为 D:/Projects/WorkflowOps。提交前再次运行 verify_import.py，698 个文件源/目标哈希及三源 Git 状态检查通过。
暂存只包含根 P1 文件和 source-manifest 的 698 个路径；.runtime、缓存、数据库、私钥、环境文件、egg-info 和嵌套 Git 不纳入。检查 Git 暂存 blob 中的凭据形态时只报告路径/规则，不输出匹配值。
Git 使用既有换行规则，可能规范化文本 blob；原工作树字节保持不变，来源清单 SHA-256 描述复制工作树字节，Contract blob 保持原哈希。此次不修改用户级 Git 配置、不添加远端、不 push。提交作者身份仅通过本次命令参数提供。

本次复用 SupportOps 与 DataCopilot 一致的 Jeremy / GitHub noreply 身份；D:/Projects 父仓库没有作者配置。默认 diff --cached --check 报告原样导入文件 4 处 EOF 空行（compatibility_baseline 两份及两个 BUSINESS_DATA 文档），保留原字节；仅关闭 blank-at-eof 检查后其余空白检查通过。暂存 713 文件，凭据形态匹配为 0，100 个文本 blob 按既有 Git 规则规范化换行，三份 Contract blob 哈希原样一致。
