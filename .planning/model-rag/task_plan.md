# 模型与真实 RAG

范围：只实施Plan第三阶段；Git分支codex/models-rag，不建立版本源码目录，不进入Agent loop/记忆/压缩阶段。

1. 基线/官方协议/安装依赖检查 — complete。
2. 三通道适配、Pi私有OAuth入口、成本门禁 — complete；三条真实模型分别通过。
3. BGE隔离GPU工作进程、版本化中文资料和40冻结样本 — complete。
4. PostgreSQL知识权威存储、Qdrant Dense + BM25/RRF/重排与证据门槛 — complete，指标/局限分别记录。
5. HTTP/CLI及各通道真实验收 — complete；119 Python/13 Node，三通道四项实际判据，ChatGPT/DeepSeek浏览器通过。
6. 证据与交付审阅 — complete；phase3-gates.json及docs/validation/models-rag.md；用户已授权本地提交与阶段标签v2.0.0-p3；不推送。

V2-3 COMPLETE，整体产品仍IN_PROGRESS。用户完成项目OAuth并实际验证gpt-6.1-sol；ChatGPT9次调用usage均可知且订阅不换算人民币。DeepSeek累计保守0.039490元，包含一次测试配置泄漏调用的未知usage最大预留补记，15元上限保持。旧环境/v1/业务数据保护复核通过。第四阶段仍NOT_STARTED，Jev仍NOT_RUN。
