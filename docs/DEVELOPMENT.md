# 开发与运行

当前源码为src/yice_agent，一套应用通过Git提交、分支和标签管理。阶段和结果以PLAN及validation为准。main和inference环境隔离；旧Conda不安装或升级。

## 已安装资源

- main：.runtime/v2/envs/main，应用与测试。
- inference：.runtime/v2/envs/inference，Torch 2.14.1+cu130，BGE GPU优先、明确CPU回退。
- Pi：bridge/node_modules，锁定依赖与JSONL协议。
- PostgreSQL：127.0.0.1:55433，workflowops_v2。
- Qdrant：127.0.0.1:6335；Ollama：11434，qwen3:8b。
- 权重与凭据：ignored .runtime/v2。源码config只放非秘密来源配置。

启动Docker Desktop及Ollama，再启动已有项目容器。根start.cmd启动8020回环HTTP；stop.cmd只停HTTP。启动不安装、迁移或重置。应用重启会清除临时演示登录，需重新选身份；私有历史与业务数据持久保留。

## 检查入口

```powershell
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py doctor
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/manage.py verify
& ".\.runtime\v2\envs\main\Scripts\python.exe" -B scripts/validation/verify.py evidence
```

verify checks编排Ruff/format、配置的13源码mypy、全部Python/真实owned PG集成、Node认证、JS语法、历史源码和diff检查；skip会使完整检查失败。普通pytest自动阻止真实模型调用。每个业务fixture只创建并清理本轮UUID schema，public与v1不能作为清理目标。

evidence核对冻结证据及行为源码的SHA-256。源码改变或ignored证据缺失时不能沿用整体PASS。它不发起新模型调用；将仓库复制到其他机器后，需重新配置并执行验收，不应伪造旧本地记录。

## 真实验收与授权

scripts/validation/verify.py live --approved顺序调用：

1. business_agent.py：三通道电商/外卖下单、支付、查询、履约、退款、审批取消、工单。
2. adversarial.py：明确标识的注入证据fixture、缺证据/冲突、伪造引用及不存在订单；不能冒充检索指标。
3. context_memory.py --phase 5 --turns 30：每通道长期对话、原始约束、纠正、工具结果、压缩。
4. memory_switch.py --phase 5：真实BGE/Qdrant记忆确认、跨会话、版本修改、向量删除故障、同会话三通道切换。

模型调用遵守项目预算：DeepSeek/Jev累计15元，可靠费率日期必需，未知usage按预留；ChatGPT订阅单列。项目OAuth由用户操作，不读取其他工具登录。禁止用未知模型名称冒充接入。

既有RAG检索基准为scripts/knowledge.py benchmark --approved；版本化资料和40冻结输入在data/knowledge。重新验证需保留前次原始证据。GPU任务用本项目跨进程串行租约，单次BGE/Qwen结束释放项目驻留，避免8GB显存竞争。Ollama请求采用num_batch=64降低提示词处理缓冲区，num_ctx仍等于应用窗口，不静默缩小上下文；该参数见[官方Runner结构](https://github.com/ollama/ollama/blob/main/api/types.go)。内存不足或模型加载失败仍明确停止。

## 维护边界

资料导入HTTP只接受内置demo-cn，由运营及CSRF保护；新增资料必须记录文件版本、来源、许可、哈希，建立并验证新索引后切换。不能混查embedding空间。

scripts/manage.py business-prepare --approved校验已有owner和fixture；replay-demo --approved每次新增一轮4订单，不清空历史。main/inference/Pi安装、下载、登录、迁移及用户级设置仍是独立授权事项。

scripts/maintenance/verify_layout.py验证889个历史Git文件；--strict只核对当时的迁移输出，后续合法源码变更后不再是当前功能门禁。版本标签和历史来源不得移动或覆盖。没有远程仓库配置时只做已明确授权的本地提交和阶段标签。

## 自研循环验收

当前运行器不导入LangGraph/LangChain；主依赖声明已移除相关框架，旧环境不卸载。普通检查包含test_react_loop及真实PG控制器。小范围真实调用为scripts/validation/react_loop.py --approved --channel all；可用--case单列失败重测，费用沿用原累计15元门禁。见[运行器设计](REACT_RUNTIME.md)。

## 项目更名后的运行

当前根目录为 D:/Projects/YiceAgent，命名及临时共享 Git 元数据的收尾见 [PROJECT_RENAME.md](PROJECT_RENAME.md)。旧容器/database/owner 标识为兼容数据资源，不表示存在另一套源码。
