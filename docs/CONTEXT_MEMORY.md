# 上下文与记忆入口

启动入口不变：根目录运行start.cmd，打开 http://127.0.0.1:8020/agent 。使用已准备的专用数据库、模型与项目登录。

1. 选择客户、模型、窗口，创建会话。切换只在空闲turn生效。
2. 输入“我偏好中文简短回答”，模型提出候选；确认后才跨会话使用。
3. 新建会话询问已确认偏好。修改立即更新权威版本，索引失败显示状态。
4. 上下文区查看输入预算、估算来源、摘要版本和省略原因。手动整理保留原始历史。
5. 订单查询显示本轮模拟业务字段。支付、取消、退款先草案后确认；审批继续走业务控制台。
6. 可以取消运行；未知结果须读取原run和业务回执核对。重复请求ID不重发模型。

代码：agent/contracts.py为封闭契约；store.py为持久化；context.py为预算摘要；memory.py为真实向量记忆；tools.py为白名单/循环；runtime.py为LangGraph；api.py为同源接口；static/agent.*为验证入口。

验收脚本需要--approved，只有已有真实调用授权和有效费率/预算时才运行：

- scripts/agent.py prepare --approved：新建专用schema，保留原业务数据。
- scripts/validation/context_memory.py --channel deepseek --turns 30 --approved：真实长会话。
- scripts/validation/memory_switch.py --approved：记忆生命周期和跨模型。
- scripts/validation/agent_drafts.py --approved：三通道草案。

结构化任务状态及有效/失效信息的生命周期见 [TASK_STATE.md](TASK_STATE.md)。当前会话状态与所选历史调用采用的状态版本分别显示，原消息仍可核对。
