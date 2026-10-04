# WorkflowOps v2

需求见 [SPEC_V2](../docs/SPEC_V2.md)，实施进度见 [PLAN_V2](../docs/PLAN_V2.md)。

V2-1 和 V2-2 已完成。当前可启动电商/外卖业务控制台：商品、订单、模拟支付/配送/退款、动作草案、客户确认、运营审批及异常工单。业务实际写入 v2 专用 PostgreSQL，全部商品、订单与政策属于本地模拟数据。

Agent 对话、三条 LLM 通道、完整 RAG、上下文管理与记忆尚未开始，不把业务控制台当作完整 v2 Agent。

## 已准备资源

- 主应用环境：`../.runtime/v2/envs/main`。
- 推理环境：`../.runtime/v2/envs/inference`，PyTorch 2.14.1+cu130；auto/GPU 优先，显存不足时报告 CPU 回退原因。
- Pi 依赖：`bridge/node_modules`，独立 lock 文件，未登录。
- 专用 PostgreSQL：`127.0.0.1:55433`，owned 数据库 `workflowops_v2`；专用 Qdrant：`127.0.0.1:6335`。
- BGE-M3 与 BGE Reranker：`../.runtime/v2/models`，固定版本与 SHA-256 核验，实际 GPU 小样本推理已通过；尚未用于本阶段业务。
- 凭据、模型、运行数据在 Git 忽略的 `.runtime/v2`，不要提交 `settings.json`。
- 旧三套 Conda、v1 数据与原源码保持不变。

## 使用已安装环境

从项目根目录执行：

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/v2.py doctor
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/v2.py up
# 浏览器 http://127.0.0.1:8020
# 健康检查 http://127.0.0.1:8020/health
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/v2.py down
```

`up` 不安装、不初始化、不重置数据；端口或自有进程状态冲突即拒绝。`down` 只停止核验归属后的 HTTP，保留数据库和模型。业务初始化已执行，无须每次重复。

新初始化或重放属于显式本地写入。在已授权的 v2 专用资源中：

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/v2.py business-prepare --approved
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/v2.py replay-demo --approved
```

`business-prepare` 校验 owner、schema 与 fixture 版本，创建业务表并用 seed 42 生成 16 商品、24 初始订单，通过业务服务推进演示生命周期。相同版本重复执行为 no_op，不覆盖已有业务。

`replay-demo` 每次创建 4 个新演示订单，运行电商/外卖送达后退款及高额审批取消剧本；结果写入 `.runtime/v2/business-replay-<run-id>.json`。不是重置命令，不删除过去演示结果。

新机器的安装、infra-up 和模型下载分别取得授权，再使用 `--approved`。只使用新 main/inference 环境，不读取其他工具账号或改用户级配置。

## 本阶段测试

从项目根目录执行；集成开关仅在当前 PowerShell 进程生效：

```powershell
$env:WORKFLOWOPS_V2_INTEGRATION = "1"
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/v2.py check
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B -m ruff check --config v2/pyproject.toml v2/src v2/tests scripts/v2.py
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B -m ruff format --config v2/pyproject.toml --check v2/src v2/tests scripts/v2.py
```

mypy 配置的源码路径相对 `v2`，因此从该目录执行：

```powershell
& "..\.runtime\v2\envs\main\Scripts\python.exe" -B -m mypy --config-file pyproject.toml
```

真实测试在 owned v2 库内建立唯一 `v2_test_<UUID>` schema，结束仅清理本轮创建的 schema。未开启集成开关时会 skip，不能称为完整业务验收通过。最新完整验收为 83 passed/0 skipped。

[第二阶段验收](../docs/V2_2_VALIDATION.md)包含实际 PostgreSQL、并发、事务、HTTP 和浏览器证据；[演示说明](../docs/DEMO_V2.md)提供手动流程。[基础阶段验收](../docs/V2_1_VALIDATION.md)和 [GPU 验证](../docs/GPU_VALIDATION.md)保留历史范围。
