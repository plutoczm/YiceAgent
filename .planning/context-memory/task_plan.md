# V2-4 上下文与记忆

状态：COMPLETE
基线：06973af / v2.0.0-p3；工作区干净。
用户授权：开始第四阶段；既有计费上限15元继续有效，不安装新依赖、不提交推送。

1. COMPLETE：检查复用边界、会话/记忆协议与持久化。
2. COMPLETE：预算装配、摘要校验、纠正保留、跨模型重组。
3. COMPLETE：确认式长期记忆、真实embedding索引与权威删除过滤。
4. COMPLETE：LangGraph受限循环、幂等turn、取消/超时与工具白名单。
5. COMPLETE：阶段交互入口、离线/真实PG/三模型30轮验收、差异审阅。

完成标准：Spec 3.2–3.5与PLAN V2-4；不标整个v2完成。第五阶段完整工作台/综合演示保持未开始。

## 错误
- 首次读取使用不存在的requirements/main.txt；依赖实际定义在pyproject.toml，后续先核对路径。

验收证据：docs/validation/context-memory.md；.runtime/v2/phase4-gates.json。三通道全部通过；未开始第五阶段，未提交推送。
