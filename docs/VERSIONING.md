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
| codex/context-memory | 第四阶段工作分支，阶段源码由v2.0.0-p4冻结 |
| codex/models-rag | 第三阶段工作分支，阶段源码由v2.0.0-p3冻结 |
| codex/directory-normalization | 本次重排工作分支；main 的原提交未覆盖 |

`v2.0.0-p2` 保留此前基础与业务节点。`v2.0.0-p3` 冻结已验证的三模型与真实RAG；Agent上下文、记忆和完整场景验收仍未完成，不能标成完整 `v2.0.0`。这些节点和新增v2.0.0-p4均为本地阶段标签，未推送。

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

需要实际切换版本时先停止当前工作台并完成或保存工作。历史源码有混合换行和冻结哈希，使用命令局部设置 `git -c core.autocrlf=false switch --detach <tag>` 保留原字节；回到当前第四阶段实现使用 `git switch codex/context-memory`。历史启动入口和环境以该版本文档为准。不要对 Git 切换执行数据库 reset。

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
