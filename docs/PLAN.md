# 忆策 Agent 实施计划

依据：[SPEC.md](SPEC.md)。本计划是用户显式授权的 v2 改造，不改变 v1 P1–P6 历史记录。用户要求实施全部 v2；阶段按顺序推进，前阶段门禁通过后再进入下一阶段。

| 阶段 | 范围 | 状态 | 门禁 |
|---|---|---|---|
| V2-1 | 独立应用、配置、PostgreSQL/Qdrant 资源、JSONL/模型协议、启动入口 | COMPLETE | 新环境 23 测试通过；真实 PG/Qdrant、重启/启动停止与文件保护通过；BGE 下载完整性已验证 |
| V2-2 | 两业务状态机、确认审批、幂等、模拟控制台 | COMPLETE | 83 测试/0 skip；真实 PG 并发、事务、HTTP、剧本与浏览器；原文件/旧环境/v1 数据保护通过 |
| V2-3 | Pi/DeepSeek/Ollama、真实 BGE/Qdrant/Reranker、资料 | COMPLETE | 三通道真实政策/意图/工具/草案；119 Python/13 Node；GPU检索重排、持久化与40冻结查询Hit@5目标通过；资源保护通过 |
| V2-4 | Context Manager、摘要、记忆、Agent Loop | COMPLETE | 三通道30轮/纠正保留；真实记忆/删除故障/跨模型；149 Python、13 Node、12源码mypy；浏览器与资源保护通过 |
| V2-5 | 工作台、集成验收、演示材料 | COMPLETE | 158 Python/13 Node；三通道12业务剧本及新30轮；真实记忆/切换；13类浏览器；冻结证据与资源保护 |

## 环境和实现范围

当前代码使用 src/yice_agent 命名空间，工作根目录为 D:/Projects/YiceAgent。主应用与 BGE 推理各建专用环境；旧 Conda 不安装或升级。初期静态/契约验证可借用已有解释器，必须标为临时验证。新专用 PostgreSQL 与 Qdrant 不连接 v1 库。Pi Node 桥接只负责模型与认证。

安装、下载、登录、迁移和计费调用按 Spec 的授权门禁处理。未具备条件不标 COMPLETE，也不把 mock 当真实能力。不提交、不推送、不删除旧代码或资源。

## 验证入口

当前入口：python scripts/manage.py doctor / check / baseline / infra-plan / up / down；显式 install、infra-up、business-prepare 与 replay-demo 使用授权标志。verify 整体验收编排已实现于scripts/validation/verify.py，manage.py verify运行普通检查；第三阶段项目专用 login-chatgpt 入口为 scripts/models/account.py。所有入口必须以未授权动作关闭为默认；凭据只在本地 ignored 配置读取，不输出正文。

## 进度和证据

2026-10-02：已核对 v1 PLAN、AGENTS 与 Git。现有 830 文件内容与 Git 状态保存至 .planning/workflowops-v2/baseline.json。SPEC 为已确认需求，尚无 v2 执行验收结果。

2026-10-02：V2-1 源码门禁：19 单测 passed；contracts/config/rpc 三源码 mypy PASS；Ruff lint PASS；830 原文件哈希一致。外部依赖/新资源等待授权，V2-1 尚未完成，不推进 V2-2。见 [V2_1_VALIDATION.md](validation/foundation.md)。

2026-10-02：V2-1 COMPLETE。新 main/inference 与 Pi 安装完成，CPU Torch；专用 PG/Qdrant 实际启动、鉴权/持久化及重启门禁通过。23 测试、核心 mypy、Ruff 通过；BGE 两模型约 4.59 GB 完整下载，固定 revision/远端 SHA-256 校验和离线元数据核对通过。未实际推理、未登录、未计费；旧三环境清单、v1 十表摘要及 830 文件一致。HTTP 已停止，专用数据资源保留。V2-2–V2-5 NOT_STARTED。见 V2_1_VALIDATION。

2026-10-03：按用户“能用 GPU 就用 GPU”调整为 auto/GPU 优先。新 inference 环境更新 PyTorch 2.14.1+cu130，CUDA 内核和 BGE-M3/Reranker 实际 GPU 样本通过；隔离协调器后连续两次完整检查 exit 0。33 单测/基础设施回归、四核心源码 mypy、Ruff 通过。失败与范围见 [GPU_VALIDATION.md](validation/gpu.md)。此为 GPU 前置补充，不标记 V2-3 RAG 或三模型验收完成。


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

2026-10-04：V2-2 COMPLETE。业务模块和本地控制台完成；固定 seed 16 商品/24 初始订单，真实执行确认、审批、支付配送退款与工单。83 pytest/0 skip、11 核心源码严格 mypy、v2 Ruff lint/format、JS 语法通过。实际回环 HTTP、主库四剧本及浏览器高额退款/工单/身份隔离/重启持久化验证通过；830 原文件、旧三环境包清单及 v1 十表保持。HTTP 8020 运行中，演示结果保留。没有新增安装、登录、计费、commit/push。V2-3–V2-5 NOT_STARTED。证据见 [V2_2_VALIDATION.md](validation/business.md)，演示见 [DEMO.md](DEMO.md)。

## 2026-10-04 目录与版本管理

按用户后续指令，当前源码使用src/workflowops、根pyproject、tests/unit和tests/integration；历史v1/v2快照仅在Git保留，不以版本目录并存。当前文档统一SPEC/PLAN/DEMO，工具为scripts/manage.py及职责子目录。需求和阶段范围不变；没有开始V2-3。路径对应及实际重排验证见STRUCTURE.md、VERSIONING.md、validation/layout.md。

## 第三阶段执行（2026-10-04）

当前分支codex/models-rag。模型协议与真实RAG实现中；用户已批准项目专用OAuth登录及DeepSeek真实验收，DeepSeek/Jev本轮累计上限明确为15元人民币，替代原100元预算。服务端配置与PG预算账本都限制15，未知usage按预留计账，费率需记录来源/确认日期。钥匙只通过本机隐藏输入，OAuth由用户完成，禁止读取旧工具登录。

真实BGE-M3 GPU入库与BGE Reranker GPU推理已执行，40冻结样本初次成功结果32有答案Hit@5=100%、未知4/4、冲突4/4；6正例被证据门槛拒答，回答覆盖率单列。Qwen真实政策引用/意图澄清/原生工具及结果回传通过；三通道整体尚未验收，不标第三阶段COMPLETE，不开始第四阶段。

2026-10-05：DeepSeek真实模型验收PASS。deepseek-flash非思考模式完成政策引用、意图澄清、原生工具与真实PG结果回传、待确认草案；实际浏览器澄清和RAG亦通过。7次调用usage均可知，累计保守估算0.010818元，剩余14.989182元；12张public业务表全行摘要不变，无遗留测试schema。ChatGPT由用户自行完成项目OAuth，目前未就绪。V2-3仍IN_PROGRESS，不进入V2-4。见validation/models-rag.md。

2026-10-05后续：修复ChatGPT模型选择的就地错误提示、登录说明、60秒页面等待/50秒后端截止与取消结算。108测试、Ruff/JS/已配置mypy及浏览器未登录交互通过；Settings和12表数据不变。普通测试继承计费配置的漏洞已隔离，一次误调用按最大预留补记后累计保守费用0.039490元。ChatGPT真实生成仍NOT_RUN，V2-3保持IN_PROGRESS。见validation/chatgpt-selection.md。

2026-10-05后续：项目Node子进程采用既有本机系统代理，实际授权服务只读探测由直连403变为200；OAuth错误记录改为固定分类且不保存凭据。119 Python/13 Node测试、Ruff和已配置mypy通过，工作台重载。真实OAuth交换仍待用户完成，本轮无新计费，V2-3继续IN_PROGRESS。

2026-10-05：V2-3 COMPLETE。用户完成项目OAuth，真实账号目录和gpt-6.1-sol生成验证通过；三模型四项CLI判据全部PASS，ChatGPT与DeepSeek实际浏览器亦通过。真实GPU embedding/重排、Qdrant持久化与租户过滤、40冻结查询Hit@5=32/32（100%）达到85%目标，证据门槛/无答案局限分别保留。119 Python/13 Node、Ruff/已配置mypy通过；最终旧环境/v1/业务表保护复核通过。DeepSeek累计保守0.039490元，ChatGPT9次订阅调用不换算人民币。证据phase3-gates.json及validation/models-rag.md。未commit/push；V2-4–V2-5仍NOT_STARTED，整体未完成。

2026-10-05：用户明确授权保存第三阶段Git版本。当前已验证源码与资料采用本地提交和阶段标签v2.0.0-p3冻结，工作分支codex/models-rag；不推送，不移动v1.0.0/v2.0.0-p2，不包含runtime、依赖、数据库或凭据。第四阶段仍NOT_STARTED。

## 第四阶段执行（2026-10-05）

用户明确授权开始V2-4，工作分支codex/context-memory，基线06973af/v2.0.0-p3。
新增独立workflowops_agent schema，不改变public业务表、v1或旧环境。实现原始历史、范围版本摘要、候选确认记忆、权威过滤与受限LangGraph、同源会话入口。
读取由真实封闭模型意图驱动固定白名单工具；草案可使用原生调用或经校验的结构化模型计划。均记录实际协议来源，不将结构化计划冒充原生tool-call。
回忆采用模型选来源ID、应用读取原文；政策采用专用Selection引用校验。BGE/Qwen按单次任务释放本项目驻留资源，以支持8GB显存串行运行。
初始148回归测试通过；后续控制器29相关测试通过，最终回归仍待运行。DeepSeek/ChatGPT30轮均已触发真实摘要并保持15元约束、CTX-B29和英文纠正；Qwen30轮仍运行，真实记忆/跨模型/浏览器和最终保护尚未完成。
早期schema、语义门禁、CPU/协议断开、摘要窗口试跑失败均保留；不得据两个通道的结果标记整个阶段完成。V2-5仍NOT_STARTED。

2026-10-05：V2-4 COMPLETE。三通道30轮及指定关键事实通过；真实BGE记忆确认、跨会话、修改、删除故障过滤、同会话三模型切换与确认状态保持通过。三通道实际Agent支付草案均待确认且订单不变；浏览器实际确认候选、召回版本2、手动压缩和取消通过。149 Python/0 skip、13 Node、Ruff/format、12源码mypy、JS、diff检查和资源保护通过。累计保守费用0.354200元/15元；ChatGPT单列。失败记录保留。见validation/context-memory.md与phase4-gates.json。未提交/推送，V2-5 NOT_STARTED，整体未完成。

2026-10-08：用户要求Git同步，随后明确选择只保存本地。第四阶段代码与已验证材料通过本地提交和v2.0.0-p4阶段标签保存；不配置远程、不推送。当前复核Ruff、12源码mypy和91个单元测试通过，无新计费调用；第五阶段仍未开始。


## 第五阶段执行与整体完成（2026-10-08）

V2-5 COMPLETE。五视图工作台、共享身份/加载门禁、run与上下文恢复、受控资料导入及验收编排完成。三通道12个双业务剧本、各30轮/摘要v4/指定事实保留、真实记忆与同会话三通道切换、对抗资料和虚构订单均通过；13类实际浏览器与PG核对通过。158 Python/0skip、13Node、Ruff/76format、配置12源码mypy、889历史源码及diff通过。Qdrant实际重启保持索引；public原行与三套旧环境保持，零遗留测试schema。v1本阶段未操作，当前逻辑十表复核NOT_RUN，历史证据保留。累计保守1.351542元/15元，ChatGPT单列。原始失败全部保留。见validation/workbench.md与acceptance-manifest.json。

五个核心阶段完成；Jev可选NOT_RUN，不增设新阶段。工作区在codex/workbench-acceptance，p4标签保持；未提交、未推送、未配置远程。后续保存Git版本须用户明确选择。

## 2026-10-09 用户要求的界面补充

现有五视图美化、响应式、上下文展示与会话选择同步完成，普通回归和实际浏览器通过。属于第五阶段后的 UI 补充，不增设阶段；旧真实调用证据保留，新 UI 记录单列。Jev 工程评估建议保持可选，中文意图对照实验尚未实施（NOT_RUN）。无新增依赖、登录、计费、提交或推送。见[界面美化与 Jev 评估](validation/ui-polish.md)。

## 2026-10-09 明亮界面与控件整改

按用户反馈替换深绿主题为浅色蓝紫工作台，并修复空状态按钮、旧上下文残留、重复命令、下拉候选、反馈和输入校验。158 Python/18 Node、实际浏览器26检查点与原业务行保护通过；本次有1次本地Qwen运行取消及真实检索，无新增计费调用。未执行按钮单列，原第五阶段与上一版UI证据保留，不增加阶段或提交版本。详见[明亮界面/控件整改](validation/ui-refresh.md)。

## 2026-10-09 正式产品界面文案

用户明确选择只完成正式产品界面。导航、账户、会话、规则与设置用语已统一，保留测试环境标识及原文出处，未启动生产能力改造。158 Python/18 Node、实际五视图与手机检查通过，原数据与历史证据保留，无计费、安装、提交或推送。见[正式产品界面](validation/product-interface.md)。

## 2026-10-09 用户授权自研 ReAct 替换

用户明确选择替换LangGraph。运行器与状态更新协议、自主调度、24步防护和元信息Trace已实现；185Python/18Node/13源码mypy与框架导入屏蔽通过。首批真实调用9/12成功，三个失败以同代码/通道/输入重测后全部通过；当前本次运行器替换COMPLETE。旧LangGraph完整30轮仍为历史，不伪装为新引擎调用。未增加平台/多Agent/生产认证，未卸载环境或提交推送。见[自研设计](REACT_RUNTIME.md)。

实际新引擎范围：185Python/18Node，三通道各4项真实模型场景，浏览器6迁移/4模型/2工具与取消；累计保守1.382392元/15元，11表原行与数量保持，无遗留临时schema。详细失败、范围与证据见[自研运行器验证](validation/react-runtime.md)。未执行Git提交/推送或生产能力扩展。

## 2026-10-09 本地 Git 版本

用户明确授权保存当前本地版本。第五阶段、后续界面完善与自研 ReAct 变更在 codex/workbench-acceptance 分支统一提交，并使用 v2.0.0-p5-react 标签冻结。不移动旧标签，不配置远程或推送，不改变验收范围。版本说明见 [VERSIONING.md](VERSIONING.md)。

## 2026-10-10 上下文结构化任务状态改进

用户选择改进第一项。已实现带原文来源与生命周期的会话任务状态、版本保存、模型候选及规则保护、有效摘要投影、当前值回忆和面板。阶段状态 IN_PROGRESS：初次配置检查204Python/18Node/14mypy通过；复核补充摘要局部失效保护，当前单测17项通过；真实模型与最终浏览器仍待验证。无安装、迁移、索引重建或Git提交。方案见 [TASK_STATE.md](TASK_STATE.md)。完整30轮及策略对比不属于本项。

2026-10-10：结构化任务状态改进 COMPLETE（本项范围）。215Python/18Node/14mypy、三通道每通道3实际turn＋1压缩通过；固定6轮历史单列，不冒充真实30轮。真实浏览器估算3718→2852、状态v4/摘要v1及用户切换通过；12表原行和数量一致、零遗留schema。费用保守累计1.455794/15元，失败全部保留。见 [任务状态验证](validation/task-state.md)。未提交/推送；完整长对话和策略对比仍未启动。

## 2026-10-10 用户授权项目更名

名称为忆策 Agent，当前目录 D:/Projects/YiceAgent，包名 yice_agent。应用/runtime 已迁移，现有数据库、索引、凭据、历史与未提交改进保留。按用户确认，旧 Codex 项目锁定的 Git 元数据暂时通过同一份 Git 指针共享；收尾命令见 PROJECT_RENAME.md。属于命名与路径迁移，不增设产品阶段，不提交或推送；更名门禁通过：215Python/18Node/14mypy、启动停止、真实GPU检索、浏览器及29表/3collection保护。Git元数据物理收尾仍PENDING；见 validation/project-rename.md。更名未执行真实LLM生成。
历史记录中的旧名称按其时间保留。
