# SupportOps AI 本地部署计划

## 目标
在 Windows 本地环境按用户指定顺序完成 SupportOps AI 的仓库分析、环境检查、独立虚拟环境、依赖安装、环境变量配置、基础设施启动、数据库迁移、质量检查及应用验证。

## 阶段状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 项目分析 | 完成 |
| Phase 2 | 本地环境检查 | 完成 |
| Phase 3 | 创建项目虚拟环境 | 完成 |
| Phase 4 | 安装项目依赖 | 完成 |
| Phase 5 | 配置环境变量 | 完成 |
| Phase 6 | 启动基础设施 | 完成 |
| Phase 7 | 数据库初始化 | 完成 |
| Phase 8 | 运行质量检查 | 完成 |
| Phase 9 | 启动并验证应用 | 完成 |
| Phase 10 | 最终部署报告 | 完成 |
| Phase 11 | 脱敏检查 LLM 与 embedding 现状 | 完成 |
| Phase 12 | 配置 Qdrant 与 embedding | 完成 |
| Phase 13 | 重启服务、同步索引并验证 | 完成 |
| Phase 14 | 分析聊天与工单 API 契约、确定本地 UI 结构 | 完成 |
| Phase 15 | 实现 FastAPI 托管的用户聊天与客服工单界面 | 完成 |
| Phase 16 | 增加 UI 路由测试并运行质量检查 | 完成 |
| Phase 17 | Docker 本地启动与界面访问验证 | 完成 |
| Phase 18 | 设计持久化双向人工会话链路 | 完成 |
| Phase 19 | 新增消息表、迁移和受控 API | 完成 |
| Phase 20 | 扩展客户与客服界面为双向会话 | 完成 |
| Phase 21 | 运行迁移、API 和全量质量测试 | 完成 |
| Phase 22 | Docker 重建与端到端人工回复验证 | 完成 |
| Phase 23 | 审查需同步的正式文档与发布范围 | 完成 |
| Phase 24 | 更新 README、架构、演示与启动文档 | 完成 |
| Phase 25 | 最终校验、提交并推送 GitHub | 完成 |

## 执行约束

- 严格按 Phase 顺序执行，每阶段结束后汇报。
- 修改文件、安装依赖、启动服务或改变系统状态前说明原因并确认。
- 不重构核心业务代码、不降低测试标准；用户于 2026-08-24 明确授权新增本地 UI。
- 用户于 2026-08-25 明确确认新增双向人工客服会话；消息需持久化并按客户身份隔离。
- 用户于 2026-08-25 明确授权同步正式文档并推送至 GitHub `plutoczm/supportops-ai`。
- 保留并尊重用户已有工作区改动。
- 中文或复杂文本自动化优先使用 Python 脚本。

## 错误记录

| 错误 | 尝试次数 | 当前处理 |
|---|---:|---|
| `python -c` 读取技能文件无输出 | 1 | 改用显式 UTF-8 只读方式读取；Phase 2 将核验 Python 命令 |
| `py` 启动器不可用 | 1 | 已找到 Conda Python；后续使用显式解释器路径 |
| 主机端口 8000 被 `datacopilot-backend` 占用 | 1 | 已按用户选择将 SupportOps 映射到 8002 |
| 主机 `curl` 访问 localhost 返回 HTTP 502 | 1 | 确认为系统代理；使用禁用代理的请求验证通过 |
| Compose API 意外使用 local reliability | 1 | 已固定 Compose 使用 Redis 服务名，健康验证通过 |
| Redis 集成测试要求主机 `127.0.0.1:6379`，Compose 未发布端口 | 1 | 已增加 localhost-only 映射，集成测试通过 |
| Compose 脱敏校验一行脚本引号转义错误 | 1 | 配置未受影响；改用无嵌套 f-string 的校验脚本 |
| 新增 UI 测试初稿残留无效词 `contains` | 1 | 在执行测试前发现并删除，未进入运行阶段 |
| Ruff 报告 `app/main.py` import 未排序 | 1 | 使用 Ruff 定向修复 import 顺序后复检 |
| pytest 无权访问系统临时目录与已有缓存目录 | 1 | 改用项目内独立 basetemp，并禁用 cacheprovider |
| 项目内 basetemp 在沙箱中仍被拒绝访问 | 1 | 经授权在沙箱外运行同一测试，2 passed |
| 双向会话新增文件的 import 顺序未通过 Ruff | 1 | 使用 Ruff 定向整理 `store.py` 和新增测试后复检 |
| 新消息表迁移与 ORM 隐式索引不一致 | 1 | 调整迁移索引定义后，在空 SQLite 数据库重新执行 `alembic check` |
| 会话恢复测试有一行超过 Ruff 100 字符限制 | 1 | 拆分为多行后复检 |
| 暂存区密钥扫描脚本被 PowerShell 引号解析阻断 | 1 | 未读取或输出秘密；改用无复杂引号的 Python 扫描重试 |
| Git 提交缺少 author name/email | 1 | 提交未创建、未推送；只读检查远程提交身份后等待用户确认本仓库 identity |
