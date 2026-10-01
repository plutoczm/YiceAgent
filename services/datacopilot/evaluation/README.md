# RAG 离线评测

`rag_cases.json` 是可版本化的最小黄金问题集，覆盖 Spark、Hive 和 Kafka。它记录问题、预期命中文档、答案关键词和参考答案。

运行：

```bash
python -m pytest -q tests/evaluation/test_rag_quality.py
```

当前离线回归评测刻意使用确定性 hashing fake embedding，以避免每次 CI 加载 3 GB 模型；生产默认 provider 和独立验证脚本使用真实 BGE-M3。离线测试覆盖项目真实的 chunk、ChromaDB 和 hybrid retrieval：

- Retrieval：Top-1 是否命中预期文档。
- Metadata：domain 过滤是否生效。
- Answer contract：回答是否覆盖黄金关键词且引用预期文档。
- Refusal：无召回结果时是否拒绝无依据回答。

答案生成在测试中使用确定性 fake LLM，因此该测试证明的是 RAG 编排、grounding prompt 与引用契约，不代表线上模型的主观回答质量。接入真实模型后，可复用同一问题集增加人工评分或 LLM-as-a-judge 报告。
