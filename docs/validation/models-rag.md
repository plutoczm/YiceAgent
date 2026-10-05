# 模型与真实 RAG 阶段记录

记录更新：2026-10-05。状态：V2-3 COMPLETE，按Plan通过三模型与真实RAG基础门禁；完整产品、长对话、上下文和记忆仍未完成。

## 当前实现

- DeepSeek 固定官方 HTTPS 目标、deepseek-flash、显式 thinking disabled；Ollama qwen3:8b 设置 think=false、num_ctx、num_predict，支持工具和 JSON schema。
- Pi-ai 1.0.0 JSONL 桥接及项目专用 OAuth Store；不读取其他工具的凭据。候选来自真实已安装注册表；ChatGPT已读取账号模型列表，并通过实际生成验证gpt-6.1-sol。
- PostgreSQL workflowops_models 独立schema保存模型调用元数据、预留、usage与保守计账；不保存提示词或模型内部思维链。
- 用户本轮授权 DeepSeek/Jev累计最多15元。配置和账本都强制15；未确认费率、未配置Key、未启用计费时拒绝调用。未知usage按预留计账，ChatGPT仅显示订阅usage，不伪造人民币价格。
- 私有 BGE JSONL工作进程采用已有权重，GPU优先、明确CPU回退；同一客户端一次只保留一个Torch模型进程，跨进程计算锁串行执行。
- 8份自编中文演示政策、2份冲突验证资料、40条首次检索前冻结的查询；不冒充真实平台官方规则。
- 真实 BGE-M3 → Qdrant Dense / 实际 BM25 → RRF → BGE Reranker。候选20/20、合并重排最多20、证据最多5；超预算减少完整证据块。
- PG workflowops_knowledge为来源与有效状态的权威，所有Qdrant查询由服务端注入tenant/index/owner过滤，向量payload不能替代业务/证据授权。
- LLM选择支持问题的原文片段，发布时检查本轮引用ID、版本、出处和逐字引用；不发布任意未验证改写。缺证据或来源冲突可直接拒答。

当前页面增加模型/知识预览、分类/检索/回答和本机隐藏密钥配置。完整Agent loop、会话记忆、上下文压缩仍属于后续阶段。

## 已执行证据

| 检查 | 当前真实结果 |
|---|---|
| 完整测试（含真实集成） | 最新119 passed、0 skipped，58.65秒；JUnit .runtime/v2/oauth-network-fix/tests.xml；另有13个Node离线检查通过；历史104/108测试证据保留 |
| 已配置严格mypy | 11个核心源码PASS；不宣称新增全部模块完成严格类型检查 |
| Ruff/格式 | 当前源码、脚本、测试检查；后续修改后复核 |
| Node/JS语法 | bridge.mjs及静态app.js通过 |
| BGE-M3 | 实际cuda:0 embedding入库；revision5617a9f61b028005a4858fdac845db406aefb181 |
| Reranker | 实际cuda:0推理；revision953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e |
| Qdrant重启 | 重启owned容器后16点仍可检索；两租户各8个不同point ID，不交叉 |
| Qwen模型 | 实际qwen3:8b政策引用、缺订单澄清、原生只读工具调用及结果回传通过；真实模型生成草案、Pydantic校验、隔离PG持久草案且未确认不新增订单亦通过 |
| 真正HTTP/浏览器 | Qwen回环HTTP、DeepSeek与ChatGPT真实页面交互已执行；ChatGPT选择按钮成功、澄清及RAG均实测通过 |
| ChatGPT | 用户完成项目OAuth；账号真实目录与gpt-6.1-sol实际生成通过，四项CLI判据及浏览器选择/澄清/RAG全部通过；9次订阅调用usage均为provider |
| DeepSeek | 2026-10-05真实deepseek-flash：政策引用、缺信息澄清、原生订单工具/结果回传、待确认业务草案全部通过；真实页面澄清/RAG亦通过，7次调用usage均可知 |
| Jev | NOT_RUN，可选，不阻塞其他本地实现 |

## 40查询指标与限制

冻结查询SHA-256：
`8a5305f34de6012fc3a86a9f248b12ea45bbe074e98f61c4687a4a2244a273c9`。

真实检索HitRate@5：32个有答案查询全部命中，100%；分母32，不把无答案/冲突塞进命中率。语料只有8个正常演示文档，结果不能外推生产资料质量。

首次成功运行的证据门槛0.35导致6个正例被拒答；原结果保存在rag-40-initial.json。使用另外8个开发样本实际校准，正例最低0.066085、负例最高0.014172，阈值取中点0.04012887并绑定reranker revision；未修改40样本。

校准后正例证据覆盖31/32、未知问题的检索拒绝3/4、冲突检测4/4。微积分问题进入弱证据候选，但真实Qwen最终no_answer，未发布业务结论，记录rag-final-refusal.json。检索命中、证据门槛与最终LLM决策分别记录，不称所有样本都回答正确。

## 保留的失败

- 两个常驻BGE/CUDA进程首次导致worker异常；改为一个模型进程后独立GPU rerank和40查询成功。
- 初次SDK接口名猜测不匹配，通过本机声明/实现读取修正。
- 初次完整测试102通过/1失败，单位证据fixture缺少新增title；补齐契约后104全部通过，未降低伪造引用断言。
- Pi Codex实现不发送原生max_output_tokens。采用minimal reasoning、流式UTF8保守预算与超限明确取消，不称精确原生token限额。
- OAuth浏览器自动读取超时；登录请求未收到项目回调。用户自行登录后才做真实通道验收，不复用已有Codex凭据。
- Qwen把否定政策误判为缺证据；抽取提示明确“否定规则也可回答”，仍严格验证每条原文引用。

## 2026-10-05 DeepSeek 真实验收

用户在本机保存Key并启用已授权调用后，实际使用deepseek-flash、thinking disabled、应用窗口8192、输出上限1536，未切换模型或使用替代凭据。

- 四个CLI判据通过：政策回答含校验引用；取消请求缺订单ID时返回澄清；原生order_lookup读取真实隔离PG状态并回传模型；propose_order经过Pydantic和业务规则生成pending_confirmation草案，未经确认不新增订单。
- 浏览器实际选择DeepSeek通道并提交意图识别和RAG问题；页面显示真实模型ID、usage、出处/版本/引用。页面RAG链路实际使用cuda:0重排，截图为.runtime/v2/screenshots/deepseek-rag-pass.jpg。
- 共7次真实计费调用均正常完成，usage来源为provider，无剩余预留或错误。按已核对峰值费率、不扣减缓存或闲时优惠的保守估算为0.010818元，预算剩余14.989182元；不是供应商账户实际扣款核账。
- public的12张业务表全行摘要与验收前一致，测试自建schema已清理。没有确认订单、支付、退款或运营动作。
- 证据：deepseek-model-smoke.json、deepseek-browser-intent.json、deepseek-browser-rag.json、deepseek-validation.json、deepseek-business-protection.json，均在ignored runtime，不含Key正文。
- 此次DeepSeek记录时ChatGPT尚未建立项目OAuth；后续已完成，见下一节。Agent长对话/记忆/压缩仍属后续阶段。

## 2026-10-05 ChatGPT及第三阶段门禁

用户自行完成项目OAuth后，从账号实际目录确认gpt-6.1-sol。回环HTTP用真实生成验证成功后才保存模型；不是仅根据注册表名称标记接入。

ChatGPT政策引用、缺订单澄清、原生order_lookup/真实隔离PG结果回传、propose_order/Pydantic待确认草案四项全部通过。浏览器实际完成模型选择成功、意图澄清及RAG引用回答，后者实际使用cuda:0重排；截图.runtime/v2/screenshots/chatgpt-rag-pass.jpg。

共9次ChatGPT订阅调用，实际模型ID均gpt-6.1-sol；provider usage累计输入2881、输出255。订阅额度单列，不换算人民币。DeepSeek/Jev预算账本累计保守费用仍0.039490元，包含此前一次未知usage的最大预留补记。

本阶段门禁汇总.runtime/v2/phase3-gates.json：
- 三条通道各自真实政策/意图/工具/草案通过，没有替代模型或mock冒充。
- BGE-M3/Qdrant/BM25/RRF/Reranker真实链路、索引持久化、两租户过滤与40冻结查询目标通过；保留前述31/32证据门槛、3/4检索拒绝的局限。
- 119 Python测试/0skip、13 Node检查、Ruff和已配置mypy通过。此次没有代码改动，沿用最近完整回归并新增实际模型/页面证据。
- 最终只读保护复核：12张public表全行摘要不变、无测试schema遗留；旧三环境包清单、v1十表、当前editable路径全部通过。证据phase3-final-protection/resource-checks.json。
- OAuth仅在项目ignored目录，git check-ignore已确认。原验收时尚未提交；随后用户明确授权以本地提交和v2.0.0-p3阶段标签保存源码，未推送。

V2-3标COMPLETE仅表示本阶段基础门禁完成。V2-4的受限Agent Loop、上下文预算/压缩、短长期记忆，以及V2-5全场景验收仍NOT_STARTED；30轮会话/跨模型会话/记忆删除过滤尚未运行。Jev仍NOT_RUN。应用health.live_verified=false及工作台整体acceptance=IN_PROGRESS不代表第三阶段失败，它们不能替代后续完整产品验收。

## 运行和账号

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -X utf8 -B scripts/knowledge.py search --query "电商取消规则"
& ".\.runtime\v2\envs\main\Scripts\python.exe" -X utf8 -B scripts/knowledge.py ask --channel qwen --query "电商发货后能取消吗"
& ".\.runtime\v2\envs\main\Scripts\python.exe" -X utf8 -B scripts/models/smoke.py --channel qwen --approved
```

ChatGPT由用户自行完成项目OAuth：

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/models/account.py login-chatgpt --approved
```

DeepSeek密钥在工作台选择运营身份后，通过“模型与知识→DeepSeek本机配置”隐藏输入；或使用本地getpass入口。不发送到聊天，不作为CLI参数。真实DeepSeek验收需要Key已配置、付费启用、费率新鲜及预算足够：

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -X utf8 -B scripts/models/smoke.py --channel deepseek --approved --paid-approved
```

所有证据在ignored runtime。三通道本阶段验收已完成；第四阶段尚未开始。

2026-10-05后续：ChatGPT“选择并验证”反馈已修复，实际未登录时会在按钮旁提示并展开说明。全量回归108通过；第一次回归因旧预算测试继承计费配置误发1次DeepSeek请求，按最高预留额补记后累计保守费用0.039490元，剩余14.960510元。详见[按钮修复与费用补记](chatgpt-selection.md)。

协议/费率来源：[DeepSeek思考参数](https://api-docs.deepseek.com/guides/thinking_mode/)、[官方中文价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[Ollama chat](https://docs.ollama.com/api/chat)、[Qdrant collection metadata](https://qdrant.tech/documentation/manage-data/collections/)、[Qdrant租户过滤](https://qdrant.tech/documentation/manage-data/multitenancy/)、[OpenAI认证说明](https://developers.openai.com/codex/auth)。Pi协议以本机已安装1.0.0源码为依据。
