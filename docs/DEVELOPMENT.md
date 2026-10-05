# 开发与运行指南

所有命令从 `D:\Projects\WorkflowOps` 执行。Python 项目配置位于根 `pyproject.toml`，源码为 `src/workflowops`。

当前基础、业务、三模型接入与真实RAG阶段已通过验收。ChatGPT项目OAuth、DeepSeek本机配置和Qwen均已真实验证；上下文、记忆与Agent Loop属于尚未开始的第四阶段。实际结果见validation/models-rag.md。

## 已安装环境

- main：`.runtime/v2/envs/main`，应用、检查与测试。
- inference：`.runtime/v2/envs/inference`，PyTorch 2.14.1+cu130，BGE GPU 优先与明确 CPU 回退。
- Pi：`bridge/node_modules`；安装清单在 `bridge/package.json` 和 lock。
- PostgreSQL：`127.0.0.1:55433`，owned 库 `workflowops_v2`。
- Qdrant：`127.0.0.1:6335`，资源声明在 `infra/compose.yml`。
- 权重：`.runtime/v2/models`；非秘密来源配置在 `config/model-sources.json`。
- 密码/API key/OAuth 状态：只在 ignored runtime，禁止放入 `config/` 或 Git。

已有 main 环境的 editable 链接已随迁移修正到根 `src`。三套旧 Conda 保持不变，历史版本使用其自身说明。

## 统一入口

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py doctor
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py up
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py down
```

也可以运行根 `start.cmd / stop.cmd`。当前工作台为 `http://127.0.0.1:8020`；旧 v1 的 8010/8011/8012 入口在 Git 历史，不作为当前启动方式。

up 不安装、不初始化、不重置；已有进程或端口冲突会拒绝。down 核验进程归属后只停止 HTTP，保留模型、数据库、卷和演示结果。

## 测试与检查

```powershell
$env:WORKFLOWOPS_V2_INTEGRATION = "1"
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py check
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B -m ruff check src tests scripts
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B -m ruff format --check src tests scripts
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B -m mypy
```

环境变量仅影响当前进程。测试分为 `tests/unit` 与 `tests/integration`；真实集成未开启时会 skip，不能算完整验收。真实测试只在 owned 数据库新建并清理本轮 UUID schema，不删除 public 表或数据。mypy 仅覆盖 pyproject 配置的 11 个核心源码。

静态页面的 JavaScript 语法检查为 `node --check src/workflowops/static/app.js`；Pi源码桥接为bridge/bridge.mjs；语法检查node --check bridge/bridge.mjs，离线回归node --test tests/unit/test_oauth_input.mjs tests/unit/test_oauth_errors.mjs。

历史保护和当前冻结布局：

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py baseline
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/maintenance/verify_layout.py --strict
```

## 显式本地业务写入

业务初始化已完成，通常无需再次执行：

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py business-prepare --approved
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py replay-demo --approved
```

business-prepare 校验 owner/schema/fixture，固定 seed 42、16 商品/24 初始订单，相同版本为 no_op。replay-demo 每次新增 4 个订单并保存一轮两类送达退款、高额取消退款结果，不清空旧结果。页面操作亦属模拟业务，先草案后确认，必要时先运营审批。

安装/下载/新资源创建保持独立授权门禁。依赖清单位于 `requirements/main.lock.txt`、`inference.lock.txt`、`inference-cpu.lock.txt` 与 `inference.txt`；不要混入旧环境。

## 模型工具

| 文件 | 作用 |
|---|---|
| scripts/models/download_torch.py | 下载锁定 CUDA wheel，官方 SHA-256 必须通过 |
| scripts/models/prepare_models.py | 匿名下载固定 BGE-M3/Reranker，owned 存储及哈希 |
| scripts/models/verify_accelerator.py | GPU/CPU 探测；--models 才实际加载本地模型 |

权重已存在。本次只验证路径/设备探测，不重新下载或重跑完整模型推理。真实模型样本结果见 [GPU 验收](validation/gpu.md)；完整RAG与三模型基础验收已通过，不能据此宣称长对话/记忆验收完成。模型/账号/计费问题按 [SPEC](SPEC.md)处理。

## 第三阶段模型与知识预览

当前三通道均已通过真实模型/RAG基础验收，实际模型为gpt-6.1-sol、deepseek-flash、qwen3:8b；各通道独立记录，不自动回退。知识与模型运行状态位于两个owned PostgreSQL schema；不会混入业务public表。

在工作台选择运营身份，展开模型与知识的DeepSeek本机配置，隐藏输入Key。用户当前授权总预算15元，后端和持久账本均限制15，未知usage按预留计账。ChatGPT由用户运行scripts/models/account.py login-chatgpt --approved并完成新OAuth链接，不复用其他工具登录。

详细协议、命令、实际结果、局限与待执行项见 [模型与RAG记录](validation/models-rag.md)。独立CLI为scripts/knowledge.py与scripts/models/smoke.py。

普通pytest自动阻止真实模型生成，预算fixture显式关闭付费；不能因本机已保存Key而触发计费。真实模型验收使用scripts/models/smoke.py显式--approved（DeepSeek另加--paid-approved），按当前预算门禁执行，不把普通单测替身计作模型质量。

Node项目子进程沿用现有本机Windows代理，不修改系统设置。1455被其他进程占用时，用户可把完整的新localhost:1455/auth/callback地址只回填到本机同一次登录终端；账号选择页面或旧state会被拒绝并允许重填。仅在项目ignored目录保存OAuth，失败诊断不包含凭据。
