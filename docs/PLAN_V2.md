# WorkflowOps v2 实施计划

依据：[SPEC_V2.md](SPEC_V2.md)。本计划是用户显式授权的 v2 改造，不改变 v1 P1–P6 历史记录。用户要求实施全部 v2；阶段按顺序推进，前阶段门禁通过后再进入下一阶段。

| 阶段 | 范围 | 状态 | 门禁 |
|---|---|---|---|
| V2-1 | 独立应用、配置、PostgreSQL/Qdrant 资源、JSONL/模型协议、启动入口 | COMPLETE | 新环境 23 测试通过；真实 PG/Qdrant、重启/启动停止与文件保护通过；BGE 下载完整性已验证 |
| V2-2 | 两业务状态机、确认审批、幂等、模拟控制台 | COMPLETE | 83 测试/0 skip；真实 PG 并发、事务、HTTP、剧本与浏览器；原文件/旧环境/v1 数据保护通过 |
| V2-3 | Pi/DeepSeek/Ollama、真实 BGE/Qdrant/Reranker、资料 | NOT_STARTED | 三真实通道、真实模型索引、40 查询门禁 |
| V2-4 | Context Manager、摘要、记忆、Agent Loop | NOT_STARTED | 长对话、跨模型、删除过滤、循环/预算/失败边界 |
| V2-5 | 工作台、集成验收、演示材料 | NOT_STARTED | 三通道完整剧本、实际浏览器、完整证据 |

## 环境和实现范围

v2 代码使用 v2/src/workflowops 命名空间，工作根目录仍为 WorkflowOps。主应用与 BGE 推理各建专用环境；旧 Conda 不安装或升级。初期静态/契约验证可借用已有解释器，必须标为临时验证。新专用 PostgreSQL 与 Qdrant 不连接 v1 库。Pi Node 桥接只负责模型与认证。

安装、下载、登录、迁移和计费调用按 Spec 的授权门禁处理。未具备条件不标 COMPLETE，也不把 mock 当真实能力。不提交、不推送、不删除旧代码或资源。

## 验证入口

计划入口：python scripts/v2.py doctor / check / infra-plan / up / down / verify / login-chatgpt。所有入口必须以未授权动作关闭为默认；凭据只在本地 ignored 配置读取，不输出正文。

## 进度和证据

2026-10-02：已核对 v1 PLAN、AGENTS 与 Git。现有 830 文件内容与 Git 状态保存至 .planning/workflowops-v2/baseline.json。SPEC_V2 为已确认需求，尚无 v2 执行验收结果。

2026-10-02：V2-1 源码门禁：19 单测 passed；contracts/config/rpc 三源码 mypy PASS；Ruff lint PASS；830 原文件哈希一致。外部依赖/新资源等待授权，V2-1 尚未完成，不推进 V2-2。见 [V2_1_VALIDATION.md](V2_1_VALIDATION.md)。

2026-10-02：V2-1 COMPLETE。新 main/inference 与 Pi 安装完成，CPU Torch；专用 PG/Qdrant 实际启动、鉴权/持久化及重启门禁通过。23 测试、核心 mypy、Ruff 通过；BGE 两模型约 4.59 GB 完整下载，固定 revision/远端 SHA-256 校验和离线元数据核对通过。未实际推理、未登录、未计费；旧三环境清单、v1 十表摘要及 830 文件一致。HTTP 已停止，专用数据资源保留。V2-2–V2-5 NOT_STARTED。见 V2_1_VALIDATION。

2026-10-03：按用户“能用 GPU 就用 GPU”调整为 auto/GPU 优先。新 inference 环境更新 PyTorch 2.14.1+cu130，CUDA 内核和 BGE-M3/Reranker 实际 GPU 样本通过；隔离协调器后连续两次完整检查 exit 0。33 单测/基础设施回归、四核心源码 mypy、Ruff 通过。失败与范围见 [GPU_VALIDATION.md](GPU_VALIDATION.md)。此为 GPU 前置补充，不标记 V2-3 RAG 或三模型验收完成。


## V2-2 执行细化

用户于 2026-10-03 明确要求继续 V2-2。只新增 v2 业务模块及本阶段控制台，不开始 LLM/RAG/记忆/UI 五视图阶段，不安装依赖或调用模型。

1. 移植原 BusinessOperations 的先资源后动作锁、快照绑定审批、过期/漂移与唯一回执语义。
2. 新建 owned v2 业务 schema 版本标记、精确金额商品/订单、动作/审批/回执、模拟事件、工单对话、幂等命令与 metadata-only 审计表。
3. 同源演示身份由服务端白名单构造，写操作需 CSRF；封闭 DTO 不接收租户、金额、授权或数据库字段。
4. 下单/支付/取消/退款/工单写入先草案后确认；高额退款及已支付取消先运营审批后客户确认。运营手动模拟支付/退款成功失败、履约推进。
5. 固定 seed 生成初始商品与 24 订单，再通过业务服务的确认和模拟事件形成覆盖生命周期的演示状态。相同 fixture no-op，拒绝覆盖不明数据。
6. 退款与配送状态分开记录；普通退款不自动撤销配送，已支付取消才原子执行取消与退款申请。明确失败后可新建退款草案再确认，未知结果禁止自动重放。
7. 隔离 PostgreSQL schema 验证 HTTP、并发、事务、取消/退款争用、过期/漂移、越权、重复请求与未知结果核对。测试仅清理本轮自建 schema。
8. 主库显式 business-prepare 后做最小控制台演示。阶段门禁和来源保护通过后记录证据再标完成。

不执行 v1 迁移/重置、无账号登录/计费/commit/push。初始化 v2 新表与模拟数据是本次已授权业务阶段的必要步骤。

2026-10-04：V2-2 COMPLETE。业务模块和本地控制台完成；固定 seed 16 商品/24 初始订单，真实执行确认、审批、支付配送退款与工单。83 pytest/0 skip、11 核心源码严格 mypy、v2 Ruff lint/format、JS 语法通过。实际回环 HTTP、主库四剧本及浏览器高额退款/工单/身份隔离/重启持久化验证通过；830 原文件、旧三环境包清单及 v1 十表保持。HTTP 8020 运行中，演示结果保留。没有新增安装、登录、计费、commit/push。V2-3–V2-5 NOT_STARTED。证据见 [V2_2_VALIDATION.md](V2_2_VALIDATION.md)，演示见 [DEMO_V2.md](DEMO_V2.md)。
