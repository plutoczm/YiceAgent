# 原仓库与历史备份退役

日期：2026-10-02（Asia/Shanghai）。用户已取消历史数据接入，选择继续使用 WorkflowOps 自生成模拟数据，并明确授权删除三个原项目目录及已有私有历史备份。本次没有新增业务功能、启用真实模型或改变租户/审批规则。

## 删除范围

- D:/Projects/AgentOpsHub
- D:/Projects/supportops-ai
- D:/Projects/DataCopilot
- WorkflowOps/.runtime/private-resources：已保全的真实配置、历史数据库、模型/训练输出、Git bundles、冷卷归档及其恢复验证副本。

不删除 D:/Anaconda3 下三个 Conda 环境、原 Docker 容器/卷/镜像、WorkflowOps 当前演示库、当前代码/契约或既有验收记录。原 Docker bind mount 可能指向已退役目录，不能据此再启动历史堆栈；这些 Docker 资源未在本次授权中要求清理。

## 独立来源验证

scripts/verify_import.py 默认只对当前 Monorepo 文件核对历史 source-manifest.json 中的 698 个冻结 SHA-256，并继续拒绝路径越界、文件漂移、嵌套 Git、数据库/密钥源文件及缺失 LICENSE。不读取原目录，不把已不存在的原 Git 状态声称为当前已验证。

保留 `--verify-originals` 作为显式历史来源审计方式：要求原文件和原 Git 完整存在，失败时不静默回退。正常 doctor、verify --offline、demo-check --all 只依赖 WorkflowOps 和原三个 Conda 环境。原 import_sources 工具属于历史导入流程，原目录删除后不再运行。

历史 SOURCE_AUDIT、P1–P6_VALIDATION、CONSOLIDATION 和 PRIVATE_RESOURCE_RETENTION 记录保留为当时事实；其中“保留原目录”“等待历史接入”“已有备份”已被用户本次决定取代，不是当前恢复保证。源码来源记录和必要 LICENSE 继续保留。

## 安全执行与验证

删除前检查四个目标的绝对路径与精确 allowlist 相同，拒绝顶层链接；递归删除不跟随内部 symlink/junction，只移除链接本身。先证明不读取原目录也能通过冻结文件哈希、原 P6 回归和 acceptance；再删除原目录，最后删除私有备份。

新增测试覆盖原目录不存在时本地来源验证成功、当前文件漂移仍失败、显式原来源审计不回退。删除完成状态与之后的独立验证结果记录在本文件末尾及 ignored .runtime/repository-retirement/deletion.json。


## 完成结果

四个精确目标已删除并确认不存在，原目录先删除、私有历史备份最后删除。删除脚本对真实 Windows junction 的临时安全检查通过：外部目标仍在，越界路径被拒绝，自有只读文件可删除。无删除错误，没有移除额外用户目录、Conda 或 Docker 资源。

实际删除后的完整 `verify --offline` 和 `demo-check --all` 均 exit 0：根 44、Harness 291、Support 219/1 skip、Analytics 284/1 skip；HTTP support/delivery/reports/ui 为 1/6/1/1 passed；E01–E24 24 passed，D5 50/50，AC-01–AC-12 通过。两项平台 skip 与既有基线相同，不计 pass。来源验证的 AC-01 退役条件已按用户明确授权更新，仅核对当前冻结文件，不声称原目录仍在或原 Git 当前状态不变。

删后报告：`.runtime/p6-844b023812ed4df4923a56d11089df8a`；实际路径删除/边界保护/最终状态证据在 ignored `.runtime/repository-retirement/`。三个 Conda 解释器和 WorkflowOps 自有演示数据库保留，当前业务行指纹前后相等，测试 schema 0，HTTP 已停止。没有真实模型请求、历史数据迁移或恢复备份保证。当前功能继续使用自身生成的 24 订单 fixture 与实际业务流程。

退役校验代码、测试和文档为本轮未提交改动；上一次本地提交 9ecdb82 保留，没有自动 push。退役完成，到此停止。
