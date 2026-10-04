# v2 复用记录

| 原文件 | 复用内容 | v2 实现 |
|---|---|---|
| services/harness/backend/src/app/agents/models.py | 可信限额与供应商无关消息契约 | contracts.py；v2 限额按 SPEC_V2 调整 |
| services/harness/backend/src/app/agents/runtime.py | 工具请求结果配对、截止时间、调用 ID 校验 | contracts.py / rpc.py；参数级循环控制尚待 V2-4 |
| scripts/demo_db.py | owner/Compose 标签、固定端口和资源校验思想 | database.py / scripts/v2.py / compose.v2.yml |
| services/harness/backend/src/app/demo/composition.py | 本地 Host/Origin 校验 | app.py |
| services/support/app/operations.py | 确认、审批、快照漂移、幂等事务 | business/service.py、rules.py、models.py；V2-2 已验证 |

这些为设计语义复用，尚未逐字复制旧应用包。旧 source manifests、LICENSE 和测试保留。MIT 来源归属见原服务 LICENSE。

## V2-2 业务语义来源

读取原 SupportOps BusinessOperations 后，在独立命名空间重新实现先锁资源再锁动作、业务版本 CAS、15分钟过期、快照哈希绑定独立审批、唯一回执与已提交结果核对。不导入旧 app 包，不将旧业务库作为 v2 运行数据。新服务额外支持电商/外卖差异、动作组合与按客户端请求 ID 的命令幂等。

语义对应的 v2 验证保留在 test_business_postgres.py：并发请求/确认、两个退款或取消退款竞争、订单/规则/商品漂移、审批后漂移、快照篡改、事务回滚、提交后丢回执以及工单权限。test_business_rules.py 验证封闭输入与金额边界，test_business_http.py 验证实际 HTTP 和演示身份。原源码及其 LICENSE/测试保持，不逐字复制旧包或迁移 v1 记录。实际结果见 ../docs/V2_2_VALIDATION.md。
