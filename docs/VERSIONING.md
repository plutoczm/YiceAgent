# Git 版本管理

项目目录只展示一套当前实现。用 Git 提交保存每个可复核增量，用分支隔离正在进行的工作，用标签冻结已验证节点。目录不另存 v1/v2/v3 副本。

## 当前版本节点

| 节点 | 含义 |
|---|---|
| v1.0.0 | 已完成的离线三服务演示及已有来源退役修改；原源码、文档、测试完整保存 |
| f048f2949c101b2f8b869728d59c4254a6eb2049 | 全面重排前第二阶段实现；889 个基线文件原字节已验证，所有未提交内容先保存 |
| v2.0.0-p2 | 单一架构、基础与双场景业务阶段及目录验证；不是完整 v2 发布 |
| v2.0.0-p3 | 三模型与真实GPU RAG基础验收节点；上下文、记忆与完整产品验收仍未完成 |
| v2.0.0-p4 | 上下文预算/摘要、确认式记忆、受限Agent循环及三通道30轮验收节点；完整v2验收仍待第五阶段 |
| v2.0.0-p5-react | 第五阶段工作台、界面完善与自研 ReAct 当前快照；新引擎通过 12 项针对性真实模型场景，旧 30 轮验收保留为历史 |
| codex/workbench-acceptance | 第五阶段与自研 ReAct 工作分支，由 v2.0.0-p5-react 保存历史节点 |
| codex/task-state-context | 会话任务状态改进及忆策 Agent 更名工作分支 |
| main | GitHub 同步采用的当前默认分支，保留完整历史 |

| codex/context-memory | 第四阶段工作分支，阶段源码由v2.0.0-p4冻结 |
| codex/models-rag | 第三阶段工作分支，阶段源码由v2.0.0-p3冻结 |
| codex/directory-normalization | 本次重排工作分支；main 的原提交未覆盖 |

`v2.0.0-p2` 保留此前基础与业务节点。`v2.0.0-p3` 冻结已验证的三模型与真实RAG；Agent上下文、记忆和完整场景验收仍未完成，不能标成完整 `v2.0.0`。这些阶段标签已于2026-10-10同步到GitHub，同步不改变各标签原有验收范围。

历史 v1 原目录、来源 manifest、原 LICENSE 和 P1–P6 记录在 Git 中。当前保留的 `licenses/` 与 `docs/provenance/` 支持复用署名和追溯。Git 不包含数据库、模型、账号凭据或依赖环境，它们由既有资源管理。

## 查看历史

不切换工作目录也能读取历史：

```text
git tag --list
git log --oneline --decorate --graph
git show v1.0.0:README.md
git show v1.0.0:docs/PLAN.md
git show v1.0.0:services/support/app/operations.py
git show f048f2949c101b2f8b869728d59c4254a6eb2049:docs/SPEC_V2.md
```

需要实际切换版本时先停止当前工作台并完成或保存工作。历史源码有混合换行和冻结哈希，使用命令局部设置 `git -c core.autocrlf=false switch --detach <tag>` 保留原字节；回到当前实现使用 `git switch codex/workbench-acceptance`。历史启动入口和环境以该版本文档为准。不要对 Git 切换执行数据库 reset。

## 后续约定

1. 新任务使用 `codex/<任务名>` 分支，版本目录保持不变。
2. 每个阶段修改后执行配置好的检查，记录真实证据，再创建本地提交。
3. 阶段标签可使用 `v2.0.0-p3`、`v2.0.0-p4`；完整验收后才使用 `v2.0.0`。
4. 不移动已冻结的发布标签来掩盖失败；修复创建新提交和新标签。本次首次创建标签的换行保护修正保留在重排过程记录中。
5. 合并/推送/发布按明确请求执行。目录重排没有推送远程，也没有改变用户级 Git 配置。

历史保护检查：

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py baseline
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/maintenance/verify_layout.py --strict
```

第一条核对不可变 Git 原始内容；第二条同时核对本次冻结的迁移后文件。后续功能开发会使第二条的冻结哈希改变，需要记录新的阶段证据，不能以旧迁移结果声称新功能通过。

2026-10-08：用户选择先同步本地Git，不配置远程仓库。第四阶段创建本地提交和v2.0.0-p4标签；不移动旧标签，不合并或覆盖main，不推送。源码、资料和测试纳入Git，运行数据、凭据、模型和依赖保持ignored。

## 2026-10-09 当前版本保存

用户明确授权保存本地 Git 版本。当前第五阶段工作台、浅色蓝紫界面、控件修复、正式产品文案与自研 ReAct 运行器合并为一次可复核提交，使用本地标签 `v2.0.0-p5-react`。保留全部旧提交和标签，不配置远程或推送。

验收依据为历史基线加本次运行器变更验收：185 Python、18 Node、13 源码 mypy，以及三通道共 12 项真实模型场景；完整新引擎 30 轮及双业务剧本未整套重跑，详见 [运行器验证](validation/react-runtime.md)。标签表示可还原的源码节点，不声明生产上线能力或新的完整模型质量验收。冻结清单中的 committed/pushed 是验收记录生成时状态，实际版本归属以 Git 标签为准。

当前源码、文档、测试与规划记录纳入 Git。私有原始调用记录、截图、数据库、凭据、模型和环境仍存于本机 ignored 资源；清单只保存路径和哈希。单独克隆仓库不会自动获得这些运行资源。已验收 SVG 通过路径级换行属性保留原字节，其他源码继续遵循既有 LF 规则。

## 2026-10-10 GitHub 同步

用户授权同步当前忆策 Agent 到已存在的公开仓库 [plutoczm/YiceAgent](https://github.com/plutoczm/YiceAgent)。当前任务状态改进与目录/命名迁移保存为新提交，main仅允许从其原节点快进到当前代码；远程为origin，推送main和五个已有阶段标签，禁止force或上传refs/codex。旧版本通过原提交和标签还原，不复制源码目录。提交使用已确认的GitHub noreply身份，仅在命令进程内设置，不修改用户级Git配置。

上传前检查当前文件和可达版本历史，凭据/数据库/环境/模型仍在ignored资源中。原模型验收范围保持；本次不执行新模型调用或宣称新生产能力。Git元数据物理搬迁仍按此前用户选择延后，共享Git指针不影响正常提交与同步。实际同步结果以remote main和标签查询为准。

同步核对：当前源码提交 e11cc0f 已进入 GitHub main；五个原阶段标签的对象和目标提交均与本地一致。工作分支已切换为 main，并跟踪 origin/main；工作区不包含runtime和凭据。后续同步可在main使用git push，开发新功能仍使用codex/任务名分支。记录时的原验收committed/pushed字段保留其生成时意义，不据此覆盖真实Git状态。
