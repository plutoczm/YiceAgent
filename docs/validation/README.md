# 验收记录

当前自研运行器变更验收见[自研 ReAct](react-runtime.md)，新调用与历史基线分开记录。历史整体结果见[第五阶段与整体验收](workbench.md)，冻结清单见[acceptance-manifest.json](acceptance-manifest.json)。本机原始证据是ignored文件，克隆仓库后不能假装拥有本机验收结果。

| 主题 | 范围 |
|---|---|
| [基础设施](foundation.md) | 隔离环境、协议、专用资源与权重下载的历史门禁 |
| [GPU](gpu.md) | 实际BGE/Reranker GPU推理、失败与回退 |
| [业务](business.md) | 第二阶段真实数据库、HTTP与浏览器 |
| [目录迁移](layout.md) | 889个历史Git源文件、目录职责与保护 |
| [模型/RAG](models-rag.md) | 第三阶段三通道及真实检索/重排 |
| [模型选择](chatgpt-selection.md) | 登录/选择反馈、超时、普通测试阻断付费的历史修复 |
| [上下文/记忆](context-memory.md) | 第四阶段30轮、记忆生命周期与跨模型 |
| [自研 ReAct 运行器](react-runtime.md) | 当前手写循环、185测试、真实三模型与历史范围区分 |
| [正式产品界面](product-interface.md) | 最新产品文案、账户/会话名称及测试环境准确说明 |
| [明亮界面/控件整改](ui-refresh.md) | 最新视觉、按钮/下拉、浏览器实测与未执行项 |
| [界面美化/Jev评估](ui-polish.md) | 五视图响应式、实际浏览器、补充证据及可选 Jev 取舍 |
| [工作台/整体](workbench.md) | 五视图、最新回归、真实业务/长对话、浏览器与冻结证据 |

旧阶段文档的命令和“尚未完成”文字属于对应日期的历史，不作为当前状态。当前执行步骤见[DEVELOPMENT](../DEVELOPMENT.md)。

最新结构化上下文改进以 [task-state.md](task-state.md) 为准；215普通检查与三通道聚焦调用单列，未执行完整30轮重验收或策略对比。

- [忆策 Agent 更名与路径验证](project-rename.md)：2026-10-10，应用迁移门禁通过，Git元数据物理收尾待旧项目释放。
