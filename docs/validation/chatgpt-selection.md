# ChatGPT 模型选择提示修复

日期：2026-10-05。范围：第三阶段模型设置的故障修复历史；当前项目OAuth与真实ChatGPT验收已通过，V2-3 COMPLETE。

## 原因与复现

项目OAuth文件不存在，实际POST /api/v2/models/chatgpt/select在约0.23秒内返回oauth_login_required，未进入模型生成，未保存模型。原页面catch只更新页面顶端notice，模型区域仍保留“正在验证”，造成无响应的观感。截图上的候选模型不代表账号已经授权。

## 修复

- 按钮旁新增可朗读状态区，失败同时替换模型输出中的等待文字；未登录时展开项目登录说明。
- 验证中禁用按钮并显示等待上限，结束后恢复；身份切换后不显示旧结果。
- 页面请求等待上限60秒；服务端账号检查和生成总截止时间50秒，生成请求本身30秒。超时与取消均关闭私有桥接进程，已开始的调用按失败/取消记录结算。
- 缺少项目登录返回409；失败或超时不保存模型配置。状态刷新失败不会冒充模型验证失败。
- 模型/索引详细JSON折叠，账号状态和操作反馈直接可见。项目OAuth由用户手动完成，未读取其他工具凭据。

## 验证

- 108 pytest通过、0 skip，58.54秒；包括未登录零调用、账号目录超时、生成超时和取消后的账本结算/进程关闭回归。失败路径的供应商替身只验证行为，不算真实ChatGPT验收。
- Ruff lint/format、JS语法及已配置11文件严格mypy通过。
- 实际浏览器选择gpt-6.1-sol后立即显示未登录原因、展开登录说明、按钮恢复可用；截图.runtime/v2/screenshots/chatgpt-selection-feedback.jpg。
- 实际HTTP返回409/oauth_login_required，约0.234秒；项目Settings哈希不变，12张public业务表全行摘要不变，无遗留测试schema，ChatGPT生成调用数仍为0。
- 证据在.runtime/v2/chatgpt-select-fix：tests-after-isolation.xml、browser.json、protection-after.json。修复前工作文件已在ignored baseline保留。

## 回归测试配置泄漏与费用补记

第一次全量回归107通过/1失败。旧预算测试从Settings.load继承刚保存的真实Key和paid_calls_enabled=true，意外向DeepSeek发出1次请求。临时测试schema已清理，该请求实际usage未保留下来；未伪造用量或把它计作质量验收。

按请求的最大预留额0.028672元补记到持久账本，错误分类validation_usage_unavailable，使用unknown usage。包含此前7次真实验收，当前累计保守估算0.039490元，15元预算剩余14.960510元；这不是供应商实际扣款核账。证据test-billing-reconciliation.json。

预算测试现在显式清除模型凭据并关闭付费。普通pytest另有自动保护，禁止真实HTTP模型生成与ChatGPT订阅生成；带live标记的测试还必须显式启用WORKFLOWOPS_V2_LIVE。修复后的108项全部通过，未新增真实模型调用。

ChatGPT实际生成成功与账号可用模型仍待用户完成项目OAuth后验证，不标整体完成。

## 后续OAuth回调连接诊断

2026-10-05：1455同时在127.0.0.1和::1由现有ssh.exe监听，项目OAuth尚未保存，项目登录进程已结束。已核对本机Pi 1.0.0的openai-codex.js：端口占用时会使用manual_code提示，支持将完整新回调地址回填到同一次登录进程，并校验state后交换凭据。无需修改SSH、回调注册URI或读取其他工具凭据。项目终端及页面说明已补充手动回填和旧流程过期后的重新生成指引。没有记录截图中的授权码或完整回调地址，没有发起新的模型调用。

## 手动回填输入校验

用户随后回填了auth.openai.com/choose-an-account，属于授权过程中的账号选择页面，尚不含授权code。原SDK把无code输入视为失败并结束流程。

项目桥接现在在本机先校验完整回调URL的origin/path、唯一code和本轮state，不把输入内容写入错误或日志。错误页面、缺code、旧state可在同一流程重新回填；最多8次输入，整体10分钟截止，终端关闭或授权拒绝时结束。总体取消信号也会终止手动等待。Pi仍负责实际PKCE凭据交换及存储，不读取其他工具账号。

8个离线Node回归全部通过，涵盖账号选择页后继续输入、旧state、重复参数、取消、EOF、重试上限及授权拒绝；bridge语法和diff检查通过。验证证据.runtime/v2/oauth-input-fix/validation.json。本轮没有重新登录或调用模型，不宣称OAuth已经成功。

## OAuth交换网络配置

后续回调输入已通过本机校验，但流程仍在凭据交换/保存环节失败；旧提示没有保留实际HTTP分类，不能从旧截图断言确切的拒绝原因。

实际只读对照发现：Node直接GET授权服务的openid配置返回403；浏览器使用已启用的Windows本机HTTP代理，经同一既有代理请求返回200。PKCE生成器离线检查verifier/challenge长度43且SHA-256关系正确。

修复为account.py与PiAdapter共用node_environment：只给项目Node子进程使用已配置的Windows HTTPS/default loopback HTTP代理；不写Windows注册表、父进程环境、SSH或用户级配置，不接收任意代理目标。显式进程设置优先，默认Windows候选必须本机且不带代理凭据。其他工具模型Key/登录目录环境变量仍剥离；TLS校验保持默认。

新诊断只写固定分类、HTTP状态或白名单网络错误码与代理启用布尔值，不保存授权码、完整回调地址、state、verifier、令牌或供应商响应正文。对新鲜OAuth交换成功的验证仍等待用户操作，不能用HTTP200探测替代账号授权。

119 Python测试/0skip通过（58.65秒）、13 Node测试通过、Ruff与配置的11文件mypy通过，新node_env另做严格mypy通过。新子进程环境实际只读GET返回200，JSONL模型目录协议通过，工作台已重启加载。本轮没有新模型计费调用，累计保守费用仍0.039490元。证据.runtime/v2/oauth-network-fix/network-probe.json、validation.json、tests.xml。

## 最终验证闭环

用户自行完成新项目OAuth。真实账号目录确认gpt-6.1-sol，回环HTTP与实际浏览器选择按钮均成功生成后保存模型；CLI政策/意图/工具/草案及浏览器澄清/RAG全部通过。9次订阅调用usage均可知，未换算人民币。此前的端口占用、错误回填及直连网络差异保留为故障历史，不再是当前阻塞。详细阶段门禁见models-rag.md与.runtime/v2/phase3-gates.json。
