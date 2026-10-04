# 单一目录迁移验收

日期：2026-10-04。状态：COMPLETE，仅目录与 Git 版本管理；没有推进模型/RAG或记忆阶段。

## 完成结果

当前仓库只保留根 `src/workflowops`、`tests/unit`、`tests/integration`、`scripts`、`docs`、`infra` 与 `bridge` 等职责目录。没有并列的 `v1/`、`v2/`、`legacy/` 或原三服务源码目录。

旧 v1 的 830 文件状态（含先前未提交修改）已在 `v1.0.0` 保存。迁移前第二阶段实现保存在 `f048f2949c101b2f8b869728d59c4254a6eb2049`，889 个基线文件均以原始字节 SHA-256 对照可达 Git blob 验证。827 个文件退出当前树但保留 Git 历史，62 个文件映射到当前树；新增导航、保护工具和测试单独记录。

根 `README`、`AGENTS`、`SPEC/PLAN` 已切换为当前实现；根 start.cmd/stop.cmd 启动停止 8020 工作台。依赖清单放 requirements，Pi 放 bridge，固定模拟资料放 data/fixtures，公开契约放 contracts/api，来源许可放 licenses。映射见 `.planning/directory-normalization/manifest.json`，结构见 [STRUCTURE](../STRUCTURE.md)。

本次新 main 环境仅修正 editable .pth/direct_url 指向根 src/项目根；不安装依赖，不修改旧三套 Conda。ignored runtime 的既有物理位置、容器、卷、模型及 API /api/v2 标识保持兼容。

## 实际验证

| 检查 | 结果 |
|---|---|
| 完整 pytest，真实集成开启 | **86 passed，0 failed/error/skip**；41.97 秒；原83业务/基础检查与3个Git保护检查 |
| 配置的严格 mypy | PASS，11 个核心源码；没有扩充为全仓库类型完成声明 |
| Ruff lint、format | PASS；34 Python 文件格式检查 |
| Node 静态 JS 语法 | PASS |
| 不可变 Git 原文件 | 889 文件 PASS，含原用户未提交内容、历史源码和许可 |
| 冻结迁移后哈希 | `verify_layout.py --strict` 实际通过后保存机器记录 |
| 实际统一启动停止 | start.cmd/stop.cmd/start.cmd 成功；HTTP /health、页面和页面声明的/static/app.js可读 |
| 当前业务数据 | public 12 表计数与逐表行摘要均与迁移前一致：29订单、16商品、38动作/回执、1工单2消息；剩余测试schema为0 |
| v1 数据 | 10 张表摘要一致 |
| 旧三环境 | 包清单76/79/166项一致 |
| 当前 import | workflowops 实际从 D:/Projects/WorkflowOps/src/workflowops 加载 |
| 文档链接 | 当前README/AGENTS/scripts与docs本地Markdown链接无断链 |
| doctor | 两环境/Pi/配置/PG-Qdrant/BGE权重就绪；GPU选择cuda:0；真实模型本次NOT_RUN |

单元/集成测试没有削弱断言、替换真实数据库或以规则模拟模型。新 Git 保护测试使用真实本地 Git 仓库，覆盖原文件已移除仍可核对、虚假原哈希拒绝、迁移目标变化检测及目录越界拒绝。

## 失败与修复记录

- 编排 helper 未跨 turn 保留，重新初始化；首次失败未执行文件写入。
- Git 默认文本过滤导致原字节校验失败；补存 no-filters blob 与可达原字节 commit tree，保留原提交，只修正本次新建且未共享的本地 v1 标签。没有改用户 Git 配置。
- 初次只读数据库命令遭 PowerShell 引号解析错误；改为编码后的 Python 后执行成功，没有修改数据库。
- services 空目录由旧 HTTP 进程的 cwd 锁定，触发 WinError 5。先核对本项目 runtime 的 owner、marker、PID、创建时间、解释器及配置引用，再停止三个旧 HTTP 进程，保留全部持久数据。
- Ruff 将布局调整后的 workflowops 识别为 first party，出现10个导入分组问题；修正分组并格式化4文件后通过。
- 手写HTTP探针错误猜测/assets路径，返回404；改为读取页面声明的/static路径后通过，无服务代码修改。

- 最终暂存diff发现原字节index中的部分CRLF被视为行末空白；当前文本物理规范为LF、CMD使用CRLF，并显式renormalize暂存。再次diff检查和严格布局哈希通过；历史Git原字节不改。

以上失败均保留范围，未计为通过。

## 证据与交付

运行机器记录在 ignored `.runtime/v2/directory-normalization/`：

- `tests.xml`：86测试的实际JUnit。
- `resource-checks.json`：HTTP/启动停止、数据与旧环境/导入检查。
- `business-baseline.json`：迁移前只读业务摘要。
- `snapshots.json`：v1与阶段2原字节Git节点。
- `final-checks.json`：最终lint/type/JS/链接/冻结布局校验。

版本节点与读取方式见 [VERSIONING](../VERSIONING.md)。最终本地阶段标签为 `v2.0.0-p2`，不宣称完整 v2 发布。活动分支 `codex/directory-normalization`，不覆盖 main 的原提交，不推送。

HTTP工作台保持运行于 http://127.0.0.1:8020。没有模型账号登录、付费调用、模型下载或数据库重置；没有创建新功能阶段。代码版本已用 Git 保存，运行数据不由 Git 管理。
