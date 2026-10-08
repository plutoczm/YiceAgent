# 目录架构与阅读路径

本仓库只保留当前应用。版本变化由 Git 管理；历史三服务与旧演示通过 `v1.0.0` 查看，重排前第二阶段快照见 [版本管理](VERSIONING.md)。目录名表达职责，不表达软件版本。

## 目录职责

| 目录/文件 | 内容 | 放置原则 |
|---|---|---|
| src/workflowops | 唯一 Python 应用包 | 业务规则、协议、服务组合在此；保持 workflowops 命名空间 |
| src/workflowops/business | 订单/支付/退款/工单、草案/审批/回执、固定模拟 | 真实业务事实与权限由确定性服务处理 |
| src/workflowops/agent | 会话、预算、摘要、记忆、受限LangGraph和私有API | 不绕过业务权限、确认或费用门禁 |
| scripts/validation | 显式授权的真实模型/记忆验收 | 假模型控制器测试与真实模型结果分开 |
| src/workflowops/static | 同源 HTML/CSS/JavaScript 控制台 | 页面不自行决定金额、归属或审批结果 |
| tests/unit | 协议、输入校验、金额/规则、设备、Git 保护测试 | 快速确定性检查 |
| tests/integration | PostgreSQL、真实 HTTP、模拟剧本与资源检查 | 显式集成开关，隔离 UUID schema |
| scripts/manage.py | 当前启动、停止、检查与显式初始化/剧本 | 统一命令入口，安装与写资源保留授权门禁 |
| scripts/process_control.py | Windows 进程身份/归属检查 | 只停止 PID/解释器/创建时间核验后的本项目进程 |
| scripts/models | 下载、权重准备与设备推理验证 | 与业务应用解耦，使用推理环境 |
| scripts/maintenance | 仓库与来源保护工具 | 不属于 Agent 可调用业务工具 |
| bridge | Pi Node 工作进程依赖 | OAuth/协议职责；Agent loop 不交给 Pi |
| config | 可公开提交的来源/非秘密元数据 | 密码、API key、OAuth token 进入 ignored runtime |
| requirements | main/inference/cpu 冻结清单 | 隔离环境，不合并三套旧 Conda |
| infra | Compose 等基础设施声明 | 保留 owned 容器、卷、数据库和端口 |
| contracts/api | 当前业务 API schema | API 版本可独立演进；目录不复制整套项目版本 |
| data/fixtures | seed 42 模拟数据与 manifest | 模拟数据可提交，实际业务库只在受控资源 |
| docs | 当前 SPEC/PLAN/DEMO/DEVELOPMENT 等 | 旧版同名文件从 Git 历史读取 |
| docs/validation | 按验收主题整理的证据 | 标注时间、真实结果、失败/skip/NOT_RUN |
| docs/provenance、licenses | 来源映射、语义复用和原许可 | 保留署名；原源码路径对应历史 Git 树 |
| .planning/directory-normalization | 本次基线、889 文件映射和检查过程 | 不充当第二套源码副本 |
| .runtime | ignored 环境、配置、模型、证据与辅助状态 | 不提交、不在整理时删除数据库/模型 |
| pyproject.toml | 构建、依赖、pytest、Ruff、mypy | 从仓库根目录执行检查 |

`.runtime/v2`、`workflowops-v2` 容器以及 `/api/v2` 保持既有兼容标识。它们不是并列源码版本；为目录重排重建环境或数据库没有必要。

## 应用包

```text
src/workflowops/
├─ app.py / server.py    FastAPI 组合、同源保护与回环监听
├─ config.py             项目根/受控资源与秘密安全报告
├─ identity.py           固定演示身份与会话
├─ database.py           PostgreSQL 连接和 owner 校验
├─ contracts.py          供应商无关模型/消息/工具契约
├─ rpc.py                隔离工作进程 JSONL 请求/响应
├─ device.py             GPU 优先、预算与明确回退
├─ business/
│  ├─ contracts.py       封闭的人类命令 DTO
│  ├─ models.py          精确金额、订单、动作、回执和审计
│  ├─ rules.py           业务状态路径、规则与阈值
│  ├─ service.py         权限、锁、幂等、确认审批与事务
│  ├─ bootstrap.py       owned schema、固定 seed 和初始化门禁
│  ├─ scenarios.py       可重放的确定性业务剧本
│  └─ api.py             同源 HTTP 与服务器身份
└─ static/               业务控制台页面
```

建议先读 `app.py`，再读 `business/contracts.py → rules.py → service.py → api.py`，最后对照集成测试了解事务、并发和错误处理。未来模型/RAG/上下文/记忆模块在对应阶段落地后按职责加入；当前不建立空的能力目录。

## 重排规则

新增代码进入上述职责目录。历史实现只存 Git，不再创建 `v3/` 或 `legacy/v1/`。变更接口或业务规则时保留语义版本和兼容约束；源代码路径变化不改变租户权限、金额、订单状态或数据库归属。

迁移清单位于 `.planning/directory-normalization/manifest.json`，记录每个原路径的原字节 SHA-256、当前路径或 Git 历史归属。旧文件先保存在可达提交中再移出工作目录，没有丢弃未知修改。
