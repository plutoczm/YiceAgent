# 发现

- 基线b6cf03f，工作区干净，当前只有src/tests/docs等职责目录。
- 统一GenerationRequest/Result、消息工具配对、JSONL超时/取消已实现；复用，供应商差异放适配器。
- Pi-ai已装1.0.0，新版Models/CredentialStore/OAuth接口必须依实际源码适配。
- Ollama实际存在qwen3:8b，digest500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41；未推理。
- DeepSeek/ChatGPT未配置，不读取别的工具凭据。
- 官方DeepSeek支持deepseek-flash且默认thinking enabled，适配必须显式disabled；Ollama chat支持think=false、format schema和token usage。
- 本阶段不能把供应商思维链/原始响应协议字段显示为上下文或日志。

- 官方DeepSeek中文费率(2026-10-04)：Flash峰值输入2元/百万，输出8元/百万，缓存命中0.04元/百万；空闲为半价。预算计账使用峰值上界，不称真实发票金额。https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
- Qdrant1.19支持collection metadata(owner/index/model)，metadata自1.16引入；每查询服务端tenant过滤。https://qdrant.tech/documentation/manage-data/collections/ 与 multitenancy/。
- OpenAI登录官方页：https://developers.openai.com/codex/auth（重定向learn.chatgpt.com/docs/auth）；Pi是项目明确选择的第三方适配，不能声称OpenAI官方SDK保证订阅访问。
- SDK精确接口检索首次猜测ModelsOptions名称失败，改rg查实际factory，不继续按旧版Pi接口编码。

Pi Codex源码buildRequestBody未发送max_output_tokens。适配器改用streamSimple、minimal reasoning及UTF8保守输出边界，超限取消并明确provider_output_limit；不称供应商原生硬token上限，实际usage由供应商统计。账号模型列表探针只用本项目OAuth，无成功响应时只标registry candidate且verified=false。
