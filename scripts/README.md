# 工具入口

从仓库根执行。脚本按职责分类，不为软件版本建立脚本副本。

| 入口 | 用途 |
|---|---|
| manage.py | doctor/up/down/check/verify/baseline；显式 install/infra-up/business-prepare/replay-demo |
| process_control.py | Windows PID、解释器和创建时间的项目进程归属检查 |
| models/download_torch.py | 锁定 CUDA wheel 下载和官方哈希验证 |
| models/prepare_models.py | 两个 BGE 模型匿名固定版本下载 |
| models/verify_accelerator.py | 设备检测、显式本地推理样本 |
| maintenance/verify_layout.py | 不可变 Git 原文件核对；--strict 校验冻结迁移结果 |
| validation/verify.py | checks/live/evidence编排；真实模型需--approved |
| validation/business_agent.py | 三模型双业务固定剧本，可按family/scenario重测 |
| validation/context_memory.py | 明确phase4/5的真实30轮及指定事实 |
| validation/memory_switch.py | 真实记忆索引生命周期与三通道切换 |
| validation/adversarial.py | 真实模型与注入资料fixture，确定性发布边界 |

命令详情见 [开发指南](../docs/DEVELOPMENT.md)。install/infra-up/业务初始化/剧本带 --approved，只在已有授权范围使用。启动不安装或重置。旧 v1 工具在 v1.0.0 标签，而不是当前目录的 legacy 文件夹。
