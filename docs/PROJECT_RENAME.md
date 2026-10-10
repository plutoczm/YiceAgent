# 忆策 Agent 项目更名

2026-10-10，经用户确认，产品名称为「忆策 Agent」，英文目录名为 `YiceAgent`。

| 项目 | 当前名称或路径 |
|---|---|
| 工作目录 | `D:\Projects\YiceAgent` |
| Python 包 | `src/yice_agent`，导入名 `yice_agent` |
| Python 分发包 | `yice-agent` |
| Pi 桥接包 | `yice-agent-pi-bridge` |
| 主应用 | `start.cmd` / `stop.cmd`，`http://127.0.0.1:8020` |
| 进程与测试环境变量 | `YICE_AGENT_STARTUP_MARKER` / `YICE_AGENT_INTEGRATION` / `YICE_AGENT_LIVE` |
| 隔离环境及本机配置 | 新目录下 `.runtime/v2` |

启动脚本以自身位置定位根目录；应用以当前包位置定位根目录。两套隔离环境中的激活脚本、console launcher、editable 安装路径，以及 BGE 本机快照路径已随迁移修复。没有重装依赖、下载权重或合并旧环境。

## 保留的数据兼容标识

`workflowops-v2` 容器/卷及 owner、PostgreSQL 数据库 `workflowops_v2`、`workflowops_agent/models/knowledge` schema、`wf_` 向量 collection、固定 seed/replay ID 命名空间和 `/api/v2` 保留。它们标识既有持久化资源，不能通过文字替换创建空资源或改变权限归属。

知识库原文、来源许可、哈希、索引和既有会话消息保持原样。浏览器在新存储键中保存会话选择，并允许读取旧会话选择作为迁移回退。旧页面登录在服务重启后需要重新选择账户。

历史 Git 标签、许可来源、原阶段规划和冻结验证记录保留当时名称。当前路径映射及修改前字节快照存于 ignored `.runtime/v2/project-rename`；更名验证不替代三模型完整验收。

## 当前 Codex 会话占用的 Git 元数据

Windows 锁住旧目录和 `.git`。用户已选择先迁移并验证，稍后完成元数据收尾。

新目录包含唯一当前源码及全部本机 runtime。新目录的 `.git` 是指向 `D:/Projects/WorkflowOps/.git` 的 Git 指针，`core.worktree` 指向新目录；两个入口使用同一 Git 元数据、分支、标签和索引，没有复制仓库或分叉历史。旧目录仅暂留 `.git`。

在 Codex 中重新打开新目录并关闭旧项目后，从独立终端运行：

```powershell
& "D:\Projects\YiceAgent\.runtime\v2\envs\main\Scripts\python.exe" -B "D:\Projects\YiceAgent\scripts\maintenance\finalize_rename.py" --approved
```

命令核对固定目录、指针、工作树和 Git 锁，只移动共享元数据并核对 HEAD/refs；仍被占用时恢复指针并返回 `PENDING`。仅尝试移除经核对的旧空目录，不递归删除。没有提交、改标签或推送操作。

不带 `--approved` 时仅检查收尾状态。`COMPLETE` 表示元数据已进入新目录；旧空目录若仍被当前程序占用，会单独报告。
