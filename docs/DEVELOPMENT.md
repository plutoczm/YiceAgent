# 开发与运行指南

所有命令从 `D:\Projects\WorkflowOps` 执行。Python 项目配置位于根 `pyproject.toml`，源码为 `src/workflowops`。

当前完成基础与业务阶段。LLM、真实 RAG、上下文与记忆尚未完成。Node 桥接依赖和 BGE 模型已准备，账号未登录。本次重排没有安装或升级依赖。

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

静态页面的 JavaScript 语法检查为 `node --check src/workflowops/static/app.js`；当前 Pi 源码桥接尚未实现，不能用 package 的空缺脚本冒充可运行协议。

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

权重已存在。本次只验证路径/设备探测，不重新下载或重跑完整模型推理。真实模型样本结果见 [GPU 验收](validation/gpu.md)；完整 RAG 和三模型仍 NOT_RUN。模型/账号/计费问题按 [SPEC](SPEC.md)处理。
