> 本页记录迁移前的实际验收；正文命令、环境和路径保留当时语义。当前目录及命令见 [目录说明](../STRUCTURE.md) 和 [开发指南](../DEVELOPMENT.md)。对应迁移前 Git 快照：f048f2949c101b2f8b869728d59c4254a6eb2049。

# WorkflowOps v2 GPU 验证记录

日期：2026-10-03
状态：GPU 优先配置与本地 BGE 小样本推理验证通过；完整 RAG 与三条 LLM 通道尚未验收。

## 已落实的变更

- 用户要求“能用 GPU 就用 GPU”，v2 默认 inference_device 从 cpu 改为 auto。
- 仅更新项目新 inference 环境：PyTorch 2.14.1+cu130，CUDA runtime 13.0；旧三套 Conda 未改。
- 官方 Windows cp312 CUDA wheel SHA-256：
  0a09031e93632d14ef49553acd6211ece3ae9cdc0969a369b8c24e3cc4d15be9。
- torch 安装锁更新到 CUDA 版本，CPU 版本快照保留在 inference-cpu-requirements.lock.txt。
- installer 按官方 wheel 哈希分段下载并安装，支持复用已校验的本地文件。
- doctor 在独立 inference 子进程中运行真实 CUDA 内核检查，返回实际设备及选择原因。

版本选择和安装依据为 [PyTorch 官方安装说明](https://pytorch.org/get-started/locally/)与官方 cu130 wheel index；具体版本和可用性以本机实际检查为准。

## 设备策略

auto：CUDA 可用、真实测试内核正常、空闲显存至少 2.5 GiB 时选择 cuda:0。否则 CPU 回退并记录 cuda_unavailable、cuda_kernel_unavailable 或 insufficient_free_vram。

只读 embedding/rerank 在 GPU OutOfMemory 时至多回退 CPU 一次，记录 gpu_out_of_memory。显式 cuda:0 模式发生不可用/OOM 时直接失败，不掩盖失败；显式 cpu 模式保留。

协调父进程不初始化 Torch/CUDA，模型各自在独立子进程中串行加载。不停止其他程序，不卸载非本项目管理的 Ollama 模型，不复用本策略重放业务写入。

## 实际验证

| 项目 | 实际结果 |
|---|---|
| 显卡 | NVIDIA GeForce RTX 5060 Ti，约 8 GB 显存 |
| 驱动 / CUDA build | driver 616.92 / PyTorch CUDA 13.0 |
| CUDA 与矩阵内核 | cuda_available=true，kernel_ready=true |
| BGE-M3 | 实际模型参数在 cuda:0；输出 2×1024 向量，有限值、归一化检查通过 |
| BGE Reranker v2 M3 | 实际参数在 cuda:0；两候选评分约 0.954223 / 0.000016，相关候选排名第一 |
| 完整串行检查 | 隔离协调器后连续两次 exit 0，两次均实际选择 GPU |
| 单测与真实基础设施回归 | 33 passed / 0 failed / 0 skipped；含确定性模拟的设备不足/OOM回退测试 |
| 类型检查 | contracts/config/rpc/device 四源码 strict mypy PASS |
| 格式与 lint | 16 Python 文件 Ruff PASS |
| 依赖一致性 | inference pip check PASS |
| 原工作保护 | 830 既有文件哈希一致，旧三环境包清单一致，v1 十表摘要一致 |

样本仅用于运行兼容性检查，不证明知识库 HitRate、长对话效果或生产稳定性。未做刻意真实 OOM 压测；OOM 分支使用确定性异常注入验证。

## 失败、调整与证据

第一次整包下载哈希不符，没有安装该文件。改为有界分段下载，按官方 SHA-256 校验通过后安装；官方 CDN 不支持 HEAD，使用单字节 Range 获取总长度。

Reranker 的直接 GPU 加载以及父进程持有 CUDA 上下文的完整检查出现原生进程异常（3221225477）。改为 CPU FP16 加载后迁移 GPU，并移除协调器的 Torch/CUDA 初始化后，连续两次完整检查通过。尚未将异常根因归结为特定库或驱动缺陷；失败记录保留。

本地 ignored 证据：
- accelerator-evidence.json、bge-m3-gpu-smoke.json、bge-reranker-v2-m3-gpu-smoke.json。
- gpu-smoke-isolated-final-20261003.log、gpu-smoke-isolated-exit.json。
- gpu-smoke-isolated-second-20261003.log、gpu-smoke-isolated-second-exit.json。
- gpu-preservation-evidence.json。
- gpu-install.log、gpu-install-local.log、cuda-wheel-download-range.log，以及失败检查日志。

## 复现

从项目根目录执行：

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/v2.py doctor
& ".\.runtime\v2\envs\inference\Scripts\python.exe" -B scripts/verify_v2_accelerator.py --models --device auto
```

第二条仅执行离线本地模型样本，不登录或调用计费服务；如果其他程序占用显存，auto 可回退 CPU，结果必须查看 selection 和 actual_compute_devices。

业务、完整 RAG、Qwen/DeepSeek/ChatGPT 验收仍属于后续阶段，本次没有开始这些阶段。
