> 本页记录迁移前的实际验收；正文命令、环境和路径保留当时语义。当前目录及命令见 [目录说明](../STRUCTURE.md) 和 [开发指南](../DEVELOPMENT.md)。对应迁移前 Git 快照：f048f2949c101b2f8b869728d59c4254a6eb2049。

# WorkflowOps v2 第一阶段验证

日期：2026-10-02

2026-10-03 补充：默认策略现已改为 GPU 优先，CUDA/BGE 实际样本及 33 回归通过，见 [GPU 验证记录](gpu.md)。下文 CPU 安装和未推理表述是第一阶段初次验收时的历史状态。
当前状态：**V2-1 COMPLETE**。本阶段环境、基础协议、专用基础设施、启动停止门禁通过。完整 v2、三通道模型和真实 RAG 尚未完成。

## 用户授权与实际执行

本轮用户授权创建项目内 main/inference 两个隔离环境、安装清单依赖及 Pi、创建 v2 专用 PostgreSQL/Qdrant 容器与数据卷，并下载 BGE 权重。不登录账号、不读取旧模型凭据、不发起计费调用、不修改旧环境或 v1 数据。

- 环境位置：.runtime/v2/envs/main 与 .runtime/v2/envs/inference。
- 两个 venv 基于现有 Python 3.12 解释器建立，不继承旧 site-packages；所核对依赖的导入路径均位于新环境。
- 依赖已记录 main-requirements.lock.txt、inference-requirements.lock.txt 与 Pi package-lock.json。
- Pi：@earendil-works/pi-ai 1.0.0；安装时禁用 lifecycle scripts，模块语法检查通过。尚无 OAuth 登录或模型桥接执行。
- 推理环境：CPU PyTorch 2.14.1+cpu；FlagEmbedding/Transformers 等导入成功。两套 pip check 均无依赖冲突。
- PostgreSQL：workflowops-v2-postgres-1，专用卷 workflowops-v2_pgdata，回环 55433。
- Qdrant：workflowops-v2-qdrant-1，专用卷 workflowops-v2_qdrantdata，回环 6335。镜像固定 v1.19.0，已记录 digest。
- 所有资源通过固定名称、owner/Compose 标签、镜像、端口与挂载校验。

## 模型准备

| 模型 | 固定 revision | 文件字节 |
|---|---|---:|
| BAAI/bge-m3 | 5617a9f61b028005a4858fdac845db406aefb181 | 2,295,435,690 |
| BAAI/bge-reranker-v2-m3 | 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e | 2,293,259,337 |

总计 4,588,695,027 字节，约 4.59 GB（4.27 GiB）。存储在 ignored .runtime/v2/models；可提交的来源和校验记录为 v2/model-sources.json，不包含本机 snapshot 路径或凭据。

通过匿名公开下载（token=False），固定 revision，使用项目独立 HF 缓存。大权重文件 SHA-256 与远端 LFS 记录相等。离线加载配置和分词器，并通过 safetensors 或 weights_only/meta 方式核对权重格式；hidden_size 均为 1024，分别核对 391/393 个权重张量。

**以上是下载完整性和离线元数据检查，不是实际 embedding、Reranker 或检索效果验证。** 本阶段没有 forward 推理、RAG 入库或 40 查询评测。依赖锁为已安装快照，真实推理兼容与质量门禁仍归 V2-3。

## 实际门禁

| 项目 | 实际结果 |
|---|---|
| 新 main 环境：契约/HTTP/安装隔离/真实基础设施测试 | **23 passed / 0 failed / 0 skipped**；WORKFLOWOPS_V2_INTEGRATION=1 |
| main 与 inference 依赖一致性 | 两套 pip check 通过 |
| 主应用与推理依赖导入归属 | 所核对的模块全部来自各自新环境 |
| 专用 PostgreSQL | 真实连接、owner 标记、重复初始化与拒绝非自有 dialect 通过 |
| 专用 Qdrant | 无 key 拒绝；新 key 可访问；UUID 测试 collection 的实际读写通过 |
| 数据重启保留 | 两个 v2 容器实际重启，PG owner 与 Qdrant 测试点保留 |
| HTTP 生命周期 | 实际启动 8020、health 校验、重复启动拒绝、归属核验停止通过 |
| 端口冲突 | 人工占用 8020 时启动拒绝，未写状态、未停止占用者 |
| 类型检查 | 新 main 的 mypy；contracts/config/rpc 三源码 PASS |
| Ruff | 12 个 Python 文件 lint/format PASS |
| 原文件保护 | 830 个基线文件 SHA-256 保持一致 |
| 原环境保护 | 三套旧环境包名/版本清单前后一致 |
| 原数据库保护 | v1 public 的 10 张表只读行数据摘要前后一致 |
| doctor | exit 0；infrastructure_ready=true、bge_weights_prepared=true；model_validation=NOT_RUN |
| Git | diff --check exit 0；配置、模型与 node_modules 均 ignored；未 commit/push |

Qdrant 门禁中的四维测试向量只验证真实存储和鉴权，**不是 embedding 或 RAG 证据**。测试仅清理本次新建的 UUID collection，未删除用户资源。

## 失败及修复

- BGE-M3 大文件第一次连接中断，保留失败日志和 .incomplete 数据，断点续传完成。
- Reranker 也有一次 ChunkedEncodingError，采用固定版本、串行文件下载与有界重试续传后完成。
- 检查器初版只考虑 safetensors，BGE-M3 实际使用 bin；增加 weights_only=True、map_location=meta 的格式检查后通过。
- 导入探测初版假定所有模块都有 __file__；LangGraph 为 namespace package，改按 __path__ 核对。
- 安装隔离回归发现一处推理 pip 调用遗漏净化 env；补齐后 23 个测试通过。
- 保存初期 lint/mypy 修复历史，不把首次失败记作通过。

证据在 .runtime/v2：install.log、dependency-evidence.json、main-import-evidence.json、lifecycle-evidence.json、persistence-evidence.json、preservation-evidence.json、model-download-attempt-1.log、model-download.log 与 models/metadata-validation.json。

## 结束状态与下一阶段

v2 HTTP 已按归属停止；专用 PG/Qdrant 与数据卷保留。三套旧环境、v1 数据和既有文件内容保持不变。模型配置仍无 DeepSeek key/费率或 ChatGPT 登录，未读取旧登录凭据，未计费。

V2-2 至 V2-5 未开始。下一阶段是双场景订单状态机、确认审批、幂等与模拟控制台；三模型/RAG/上下文/UI 必须按各自后续门禁验收。
