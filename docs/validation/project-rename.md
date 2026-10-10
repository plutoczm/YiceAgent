# 忆策 Agent 更名与路径验证

日期：2026-10-10。范围：产品名称、当前包/目录、启动及隔离环境路径。状态：应用迁移和验证完成；旧 Codex 项目锁定的 Git 元数据收尾 PENDING，用户已明确选择稍后处理。

| 检查 | 实际结果 |
|---|---|
| 新根目录和唯一应用包 | `D:/Projects/YiceAgent` / `src/yice_agent`，可编辑安装实际导入正确 |
| main / inference 环境 | 65 项路径/launcher/本机快照记录修复，未重新安装或下载 |
| 配置检查 | Ruff/format、14 个已配置严格 mypy 文件通过 |
| Python / Node | 215 Python 测试、18 Node 测试全部通过，零 skip；4 个页面 JS 语法通过 |
| 历史来源 | 889 历史 Git blob 哈希通过；原文件 197 项全部可定位，改前字节在 ignored zip 保留 |
| 启停 | 从项目外实际执行 `stop.cmd` / `start.cmd`，均 exit 0；新目录 8020 HTTP 实际就绪 |
| 环境 launcher | main pip/pytest/mypy，inference pip/accelerate 实际运行通过 |
| 真实 RAG | 已有索引上 BGE-M3 实际 CUDA 1024 维查询、Qdrant 召回、BGE Reranker 实际 CUDA 重排 8 项，返回 3 项有效证据；未写向量或重建索引 |
| 数据保护 | PostgreSQL 29 表全行摘要/数量及 3 个 Qdrant collection 配置/数量一致，零遗留测试 schema；设置与项目 OAuth 文件原字节一致 |
| 浏览器 | 五视图新名称/导航、账户选择、旧会话/摘要/用户纠正、运行记录、模型与知识状态按钮实际检查；ChatGPT 项目授权仍显示已登录 |
| Git | HEAD、用户分支和标签保持；新工作目录使用原 Git 指针，没有复制元数据或提交/推送 |

旧目录仅包含共享 `.git`，无旧源码、环境或模型副本。Codex 自身管理的临时 turn-diff refs 随应用操作变化，不修改或恢复这些 refs；它们不等于用户提交/分支/标签变动。

Windows 第一次整目录改名被当前进程 cwd 锁阻止。内容迁移曾被 `.git` 与项目自启动 Ollama 的旧日志阻止，各次均回滚。用户确认共享 Git 后，仅停止已核对 PID/创建时间/命令/日志归属的 Ollama 服务，迁移后用原模型目录 `D:/Ollma/models` 与进程局部配置恢复。未终止 Codex 或其他项目进程。

初次数据库保护脚本遇到 psycopg `%` 占位符错误，改为绑定参数后通过。首个真实检索已返回 `grounded`，验证脚本错误地预期 `evidence`；保留初次输出，按源码契约修正后重测通过。首次 format 检查提示两个文件需格式化，修正后通过。原始失败与后续结果分别保存。

未发起 LLM 生成、ChatGPT 订阅生成或 DeepSeek/Jev 计费调用；真实三模型生成、完整 30 轮及 40 查询基准本次 NOT_RUN。更名结果不替代历史模型验收，不改变原五阶段产品完成范围。

记录：ignored `.runtime/v2/project-rename`，命名与 Git 收尾说明见 [PROJECT_RENAME.md](../PROJECT_RENAME.md)。原 141 项冻结验收 artifact 字节全部保持；清单仅增加更名补充及当前源码路径/哈希，原清单字节在改前 zip 保留。
