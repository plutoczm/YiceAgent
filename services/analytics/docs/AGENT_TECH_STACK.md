# DataPilot-AI Agent 技术栈

本文说明项目如何覆盖从大模型基础、Prompt、RAG、工具调用、Agent 编排到 Docker 上线的完整链路，并明确当前实现与后续扩展的边界。

## 1. 大模型基础

- **Prompt**：发给模型的指令和上下文。项目为 RAG、Text2SQL、SQL 审核、数仓设计分别维护角色、约束、输出格式和领域上下文，避免一个超长通用 Prompt 承担所有任务。
- **Token**：模型输入输出和上下文窗口的计量单位。接口统一返回 `prompt_tokens`、`completion_tokens` 和 `total_tokens`，便于监控成本。
- **Temperature**：RAG 和结构化任务使用低温度以提高稳定性；开放式对话可适度提高温度。
- **Embedding**：FlagEmbedding 真实加载 `BAAI/bge-m3`，输出 1024 维 dense 向量；CPU 自动禁用 FP16，GPU 可配置 FP16。
- **微调与 RAG**：缺少动态事实和私有知识时优先 RAG；需要稳定改变表达风格、格式遵循或领域能力时考虑 SFT/LoRA。当前项目实现 RAG，未内置微调训练流水线。

## 2. Prompt 工程

Prompt 采用四层结构：角色设定、任务目标、硬约束、输入上下文。Text2SQL 等结构化任务还包含少量输入输出示例（Few-shot）和 JSON 输出约束。

CoT 是通过提示词要求模型展示或执行分步推理的手段，会增加延迟和 Token；Thinking 是模型内部推理机制，两者不能混为一谈。生产系统不依赖输出隐藏思维过程，而是要求模型返回可验证的结论、依据、SQL 或引用。

## 3. 上下文与记忆

项目使用三层 Agent memory：

1. 工作记忆：单次 LangGraph 的 `AgentState`，任务结束即释放。
2. 短期与状态记忆：Docker 模式由 Redis AOF 按 `user_id + session_id` 保存最近消息、摘要和最近 Agent checkpoint，供多实例共享。
3. 长期与规则记忆：显式长期偏好保存到 ChromaDB+BGE-M3；global/user 规则保存到 Redis，并按优先级合并为 system context。

客户端仍可显式传 `history`。服务端会话可通过 `DELETE /api/v1/agent/sessions/{session_id}?user_id=...` 清除；长期记忆和规则也有显式写入/删除 API。`user_id` 当前是应用层标识，生产环境必须由认证系统提供并完成租户隔离。

## 4. RAG 流水线

```text
PDF/DOCX/TXT/MD -> 结构化加载 -> 语义边界切分 + overlap
                 -> BGE-M3 dense 向量 -> ChromaDB

问题 -> Dense ANN -------------------+
     -> BM25 关键词召回 --------------+-> RRF 融合 -> 词项重排 -> Top K
                                                     -> 阈值过滤 -> Grounded Prompt
```

`retrieval_mode=vector` 使用纯稠密向量；默认 `hybrid` 同时使用语义和关键词召回。融合阶段使用 Reciprocal Rank Fusion，随后结合词项覆盖度进行轻量重排。低于 `score_threshold` 或没有候选时直接返回“上下文不足”，不会调用模型编造答案。

提高效果的主要手段是：按标题/段落等结构切分并保留重叠；扩充候选池；混合召回；Rerank；设置低分兜底；用 Precision@K、MRR 和人工答案正确率持续评测。

## 5. 工具调用（Tool Calling）

四个专业工具由 LangChain `StructuredTool` 注册：`query_knowledge_base`、`generate_sql`、`review_sql`、`design_data_warehouse`。每个工具都有 Pydantic 输入模型并自动生成 JSON Schema，可通过 `GET /api/v1/agent/tools` 查看。

参数错误治理分四层：调用前 Schema 约束；执行前 Pydantic 校验；失败后返回可观测错误；LangGraph 设置全局 `max_steps`，避免无限重试和 Agent 循环。Function Calling 是模型原生的 JSON Schema 调用能力；Tool Calling 是更宽泛的外部能力调用机制。本项目的确定性路由选择工具，工具边界与原生 Function Calling 使用相同的结构化 Schema。

## 6. Agent 与 LangChain

- LangGraph：任务规划和有向状态图，支持 Text2SQL 后自动进入 SQL Review 的多步工作流。
- LangChain Core：`StructuredTool`、工具描述与参数 Schema。
- Memory：Redis AOF 的短期消息/摘要/状态与规则记忆，ChromaDB+BGE-M3 的显式长期语义记忆。
- Observability：返回意图置信度、路由路径、工具调用次数、耗时、错误和 Token 使用量。
- Safety：图递归步数上限、SQL 静态校验、RAG 引用和低置信度兜底。

当前是一个主管路由器加专业工具的单 Agent 架构。Multi-Agent 可将 RAG、SQL、审核拆成独立 Agent 并共享 state，但必须增加无环拓扑、全局深度上限、调用链 ID 和明确终止条件。重要操作还应增加人工确认，而不是仅靠多个 Agent 投票。

## 7. 稳定性、延迟和成本

- 格式：Pydantic/JSON Schema 校验，确定性 SQL 规则二次检查。
- 临时错误：DeepSeek 和 Ollama Provider 使用超时、有限重试和指数退避。
- 延迟：SSE 增量输出；Embedding 和切分离线执行；候选检索不调用 LLM。
- 成本：低温度、上下文裁剪、摘要压缩、模型 Provider 可切换。生产环境可继续增加 Prompt Cache、模型分级和 Batch API。
- 幻觉：Grounded Prompt、引用、阈值拒答、SQL 规则校验；高风险写操作应加入人工确认。
- 安全：不要把模型输出直接当作可执行命令；工具参数必须校验；外部服务需要鉴权、租户隔离和审计。

## 8. 评测

`backend.app.application.evaluation` 提供意图准确率、检索 Precision@K、MRR 和工具成功率。生产评测还应覆盖答案忠实度、SQL 可执行率、SQL 结果正确率、P95 延迟、单请求 Token 成本和真实用户反馈。评测集要按 RAG、Text2SQL、Review 等任务分层，不能只看一个总分。

## 9. Docker 与本地模型

默认使用 DeepSeek。启用本地 Ollama：

```bash
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull qwen3
LLM_PROVIDER=ollama OLLAMA_ENABLED=true OLLAMA_MODEL=qwen3 docker compose --profile ollama up -d
```

后端、前端、ChromaDB、Redis 和 Ollama 都有健康检查、资源限制、持久化目录和统一网络。BGE-M3 权重挂载在 `models/`，Docker 使用 CPU PyTorch wheel 避免额外 CUDA 依赖。生产环境还需在反向代理层配置 TLS、鉴权、限流和集中日志。

## 10. 能力边界

已实现：Prompt 模板、Token 统计、真实 BGE-M3 dense embedding、文档切分、ChromaDB、Redis 三层记忆、混合检索、RRF/轻量重排、引用与拒答、结构化工具、LangGraph 多步规划、SSE、评测指标、Docker、Ollama。

扩展项：可信认证/租户隔离、LLM 意图分类、Cross-Encoder/LLM Rerank、BGE-M3 sparse/ColBERT、Qdrant 原生稀疏索引、Multi-Agent 协作、LoRA 微调训练、线上 tracing 与人工审批工作流。
