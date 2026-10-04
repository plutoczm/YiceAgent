# 原配置、数据库与非默认资产保全

> 后续决定（2026-10-02）：用户已取消历史数据接入，并授权删除本记录中的私有历史备份及三个原项目目录。下文是此前保全和兼容评估的历史记录；不再代表当前存在备份或等待迁移。当前状态见 [REPOSITORY_RETIREMENT.md](REPOSITORY_RETIREMENT.md)。

日期：2026-10-02（Asia/Shanghai）。用户授权将三个原项目的真实配置、数据库及非默认资产迁入 WorkflowOps，并提交公开仓库文件。真实配置/历史数据/模型/历史 Git bundle 保存在根 `.runtime/private-resources/`，由 Git 忽略；公开提交不包含这些内容或真实配置值。

## 已迁入的私有资源

最新目录由 ignored `.runtime/private-resources/latest.json` 指向。每轮使用独立 UUID，不覆盖已有文件；详细源路径、目标路径、流式 SHA-256 和验证结果在该目录的 `manifest.json`。

- 4 份真实配置：Harness、Support、Analytics 根 `.env` 及 Analytics `docker/.env.production`，按原字节保留，未输出配置值，未自动加载。
- 90 份原文件，合计约 7.1 GB：配置、本地数据库原文件、Chroma collection、已有 BGE-M3/Qwen 权重、训练数据/输出和 Harness 顶层实测报告。未复制 Conda、虚拟环境、构建、日志或临时测试目录，没有下载、训练或调用模型。
- 2 个 SQLite 事务一致备份：Support `supportops.db`、Analytics Chroma `chroma.sqlite3`。使用只读源连接与 SQLite backup API，完整性检查为 ok，表行数与来源相等。Chroma 的关联索引文件也保留在原目录结构副本中。
- 6 个只读冷卷归档：Harness 的 PostgreSQL/Redis/Qdrant，Support 的 PostgreSQL/Redis/Qdrant。来源容器均停止，临时归档容器使用本地现有镜像、`--pull never`、`--network none` 和只读源卷，不启动或修改原服务。
- 3 份 `git bundle --all` 备份，均通过 `git bundle verify`。它们可能包含历史敏感文件，因此也仅存于 Git 忽略目录；原仓库及未提交实现不删除。

两个 PostgreSQL 冷备份已恢复到私有临时副本，并在无网络的临时容器中成功启动、只读检查 schema、停止；原 PostgreSQL 容器保持停止。数据恢复后的副本不是当前工作台业务库。

## 直接使用历史配置的兼容评估

用户选择希望当前工作台使用原配置与历史数据。评估确认这不是连接字符串替换：

| 项目 | 已观察到的状态 | 接入影响 |
|---|---|---|
| Support SQLite | 3 个订单，旧表没有 tenant_id、新版审批及操作字段 | 需要明确租户所有权、schema 迁移和客户身份映射 |
| Support PostgreSQL | 冷备份恢复后同为旧 schema，含旧 alembic_version | 不能直接作为当前受控业务库使用 |
| Harness PostgreSQL | 原配置指定数据库 public schema 为空 | 不提供当前 Support 权威业务事实 |
| Analytics SQLite | Chroma 知识/向量集合与元数据 | 应继续作为非默认知识资产，不能当受控业务报表数据库 |
| 原模型配置 | 启用了真实 provider、Qdrant/OpenAI embedding、部分自动建表/seed | 必须单独确认模型模式及初始化行为，不能静默启动、填入模拟数据或假称模型成功 |
| 当前工作台 | 明确 offline-only 装配、固定服务器身份映射、独立演示 PG | 需要经过批准的兼容配置/身份适配；原 P6 测试配置应保留 |

缺失的 tenant_id 是授权边界，不能从历史记录猜测或由 LLM 决定；旧 schema 的迁移也不能通过自动 create_all 伪造完成。当前接入状态为 **PENDING：等待用户明确兼容迁移范围和历史数据租户归属**。历史资源已迁入并验证；尚未启用为当前业务库，未将这一步说成完成。

## 恢复方式与提交边界

1. 先读取私有 manifest，核对 SHA-256 和具体 PostgreSQL 镜像/PG_VERSION。
2. 配置只加载指定私有副本；不能将真实 `.env`、密钥或数据库复制到公开源码位置。原配置中的自动建表/seed 不应直接作用于保留数据。
3. SQLite 恢复使用 `sqlite/<role>/...` 的一致备份；Chroma 还需原 collection 索引目录。PostgreSQL TAR 只能恢复到新副本/新卷并使用兼容主版本；不得覆盖原卷或现有 P6 库。
4. Git 历史恢复可从 `git/<role>.bundle` 创建独立检出，原有效未提交实现已在 Monorepo 保留；这不合并原 Git 历史到当前根仓库。
5. 本轮只提交源码、必要非默认参考文件、配置模板和脱敏迁入/评估记录。`.runtime`、实际配置值、SQLite/TAR/模型/bundle 及原私有数据不进入 Git，不 push。

原目录、原容器/卷、用户级配置和三个 Conda 环境保持不变。本记录仅证明保全、备份和兼容评估，不证明历史数据工作台或真实模型已可用。
