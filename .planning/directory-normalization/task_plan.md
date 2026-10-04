# 单一架构与 Git 版本管理

用户于2026-10-04要求全面重排，版本使用Git而非目录。

1. 当前工作树/资源基线及依赖 — complete。
2. 保存 v1 和未提交 v2 到本地Git提交 — in_progress。
3. src/tests/scripts/docs/infra/bridge 单一目录迁移与引用修正 — pending。
4. 测试、类型、lint、真实HTTP/DB、Git保存与路径验证 — pending。
5. 当前布局提交、v1.0.0与v2.0.0-p2标签、结构/版本说明 — pending。

本次Git版本管理请求授权必要本地提交/分支/标签；不推送。不增加功能、不进入模型/RAG阶段、不安装依赖、不动旧三环境或数据卷。Git快照确认完整后，旧版本文件才退出当前工作目录。
