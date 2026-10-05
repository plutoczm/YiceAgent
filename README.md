# WorkflowOps

本机学习与面试演示项目：以电商、外卖订单为场景，构建可解释的业务 Agent，重点展示受控工具、真实 RAG、上下文与记忆管理。

当前完成基础设施与确定性业务阶段：下单、模拟支付、履约推进、取消、整单退款、确认审批及异常工单。第三阶段三模型与真实 RAG 已通过基础验收：ChatGPT gpt-6.1-sol、DeepSeek deepseek-flash、Ollama qwen3:8b，以及 BGE-M3/Qdrant/BGE Reranker 的真实 GPU 检索链路。Agent 对话与上下文/记忆属于后续阶段，尚未完成。

源码只保留一套当前架构。v1、v2 与后续实现通过 Git 提交、分支和标签保存，不维护版本目录。

## 启动

已安装环境下，在项目根目录运行：

```text
start.cmd
```

打开 [本地工作台](http://127.0.0.1:8020)。停止使用 `stop.cmd`，数据库、模型与演示结果保留。Python 入口为：

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py doctor
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py up
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py down
```

既有运行资源仍位于 ignored `.runtime/v2`；保留该路径是为了兼容已安装环境和资源，不用于源码版本管理。

## 目录

```text
WorkflowOps/
├─ src/workflowops/      Python 应用：HTTP、业务、协议、身份、设备与页面
├─ tests/               unit/ 与 integration/，共享真实数据库 fixture
├─ scripts/             manage.py；models/ 与 maintenance/ 工具
├─ bridge/              Pi Node 桥接依赖与协议入口
├─ config/              可提交的模型来源元数据；不放账号凭据
├─ requirements/        主应用、推理依赖及冻结版本
├─ infra/               本项目 PostgreSQL/Qdrant Compose
├─ contracts/api/       当前公开业务接口契约
├─ data/                fixtures 模拟数据；knowledge 版本化资料与冻结查询
├─ docs/                当前 Spec、Plan、演示、架构和验收
├─ licenses/            复用来源的许可与署名
├─ .planning/           本次重排的基线、映射与过程记录
├─ .runtime/            ignored：环境、模型、配置、数据库辅助与运行证据
├─ pyproject.toml       唯一 Python 项目和测试/lint/type 配置
└─ start.cmd / stop.cmd 当前工作台的统一入口
```

完整职责与代码阅读路径见 [STRUCTURE](docs/STRUCTURE.md)。

## 阅读顺序

1. [Spec](docs/SPEC.md)：产品范围、模型/RAG、上下文、业务与安全边界。
2. [Plan](docs/PLAN.md)：阶段状态与真实验收门禁。
3. [演示说明](docs/DEMO.md)：客户/运营、确认审批、订单和工单流程。
4. [开发指南](docs/DEVELOPMENT.md)：启动、测试与工具入口。
5. [Git 版本管理](docs/VERSIONING.md)：历史版本和后续开发约定。

当前接口仍使用 `/api/v2`，Docker 标识及数据库端口保持兼容。接口版本与源码目录布局是不同职责。

## 验证与范围

历史基础、GPU、业务阶段证据保存在 [validation](docs/validation/README.md)，目录迁移的实际验证见 [layout](docs/validation/layout.md)。三通道模型/RAG基础验收已过；长对话、上下文与记忆尚未完成。各通道实际结果与失败单列于 [模型与RAG记录](docs/validation/models-rag.md)，不能由业务测试替代。

v1 的固定工作流、快照分析、原三服务及完整历史验收可通过 `v1.0.0` 查看。来源许可在 `licenses/`，复用语义见 [REUSE](docs/provenance/REUSE.md)。不重置旧数据库，不合并或修改三套旧 Conda，不自动登录、安装、计费或推送。
