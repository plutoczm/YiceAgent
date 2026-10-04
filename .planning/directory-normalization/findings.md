# 目录检查发现

- 当前源码：v2/src/workflowops，历史三服务 services/harness、support、analytics。
- docs 根层混有 v1 Spec/Plan、P1–P6 证据、v2 Spec/Plan/验收以及来源和整合记录。
- scripts 根层混有 v1 调度与 v2 环境/模型工具；start.cmd/stop.cmd 当前启动 v1 8010，v2 为 scripts/v2.py up/down 8020。
- 根 README 目前只介绍 v1，容易误认当前开发方向。
- v2 根层有依赖 lock 和桥接，src 含业务、静态 UI、配置/RPC/设备模块；暂不需要拆成更多微服务。
- 存在用户未提交修改，已记录当前哈希与状态。旧 .planning/workflowops-v2 基线、来源 manifest 和验收记录须保持可追溯。
- Remote Desktop Commander 已选择本机 Jeremy；其他设备未使用。

- v2 工具通过 __file__.parents[1] 定位根；若分类到 scripts/v2 必须调整为 parents[2]，旧入口需保持模块测试和 CLI 的行为。
- 旧 830 文件保护覆盖根 README，结构修改必须以精确迁移清单记录已授权的改动，原基线不重写，不用宽泛跳过。
- v2 单测动态加载旧脚本路径；兼容入口需复用同一实现，避免重复维护。
- 已准备兼容式 proposal.json，尚未执行；等待用户选择。

## 新决策

用户否定版本目录，选择单一当前布局。v2/src迁至src、v2/tests迁至tests，当前文档使用无版本名SPEC/PLAN/DEMO，工具使用manage.py和models子目录。原服务/历史记录只在不可变Git提交与v1标签保存；保留原LICENSE署名及当前语义复用记录。runtime/v2与Docker资源保留既有物理位置，作为ignored运行资源兼容，不用于源码版本管理。新main环境editable .pth需修正到src；不安装新包。API /api/v2保持兼容。

仓库当前无Git作者配置，后续提交沿用最近提交的作者身份，仅通过命令局部配置传入，不改用户配置。
