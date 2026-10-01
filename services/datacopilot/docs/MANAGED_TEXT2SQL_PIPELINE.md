# Managed Text2SQL Pipeline（D3）

## 状态与目标

D3 将 D1 的 trusted workflow/context 与 D2 接受的 Business Data Contract v1 catalog 接到现有 Text2SQL 生成能力，并在生成后用确定性规则构建 `ManagedBusinessSQLPlan`。此计划代表 SQL 已通过当前 catalog scope 和策略检查；它不代表 SQL 已连接数据库、解释执行或返回业务结果。

```text
BusinessAnalyticsRequest + trusted BusinessAnalyticsContext
    -> BusinessAnalyticsWorkflow.prepare()
    -> trusted scoped BusinessCatalogSnapshot
    -> policy-filtered EffectiveBusinessCatalog
    -> deterministic schema renderer
    -> ManagedSQLGeneratorPort
    -> Text2SQLService managed adapter
    -> untrusted SQL candidate
    -> dialect-aware AST authorization
    -> existing SQL structural validation and row cap
    -> ManagedBusinessSQLPlan(status=READY)
```

通用 `/api/v1/text2sql` 和 Agent tool 仍保留原有输入与行为。D3 增加独立的 application pipeline 和 infrastructure adapter，没有把 managed authorization 伪装成通用 Text2SQL 的能力，也没有把它接入生产入口。

## 确定性与模型边界

确定性部分包括 catalog 来源与 scope、classification policy、effective catalog、schema 输出、SQL dialect 选择、AST 解析、dataset/field allowlist、只读语法规则、LIMIT 规则和校验结论。相同 typed snapshot、engine 和 policy 会产生相同 schema 文本；排序不依赖 set/dict 遍历顺序，不包含 tenant 行数据、request ID、时间戳或文件路径。

LLM 只根据已经确定的逻辑 catalog 和用户问题提出 SQL candidate 及说明。candidate、说明和 token usage 都是生成结果，不构成授权证据。所有 candidate 必须重新通过确定性验证。外部调用者不能经 `ManagedSQLGenerationRequest` 设置 tenant、contract、dataset scope、field classification、database selector、credentials、RAG、LIMIT policy 或任意 schema。

## Effective catalog 与 classification

pipeline 先调用 D1 `BusinessAnalyticsWorkflow.prepare()`，并从其内部结果取得同一个 `BusinessCatalogSnapshot`。snapshot 不进入 `BusinessAnalyticsResult.model_dump()`。policy 根据 server-controlled `allowed_classifications` 过滤字段；默认允许 `public`、`internal`、`confidential`，默认拒绝 `restricted`。若过滤后 dataset 没有字段，或关系/引用指向被移除的 dataset/field，则 pipeline fail closed 或移除该关系/引用。

renderer 与 AST authorizer 共用同一个 `EffectiveBusinessCatalog` 实例。renderer 不会自行从 request、原始 contract 文件或路径重建 scope。

## Schema renderer 与 prompt 边界

renderer 输出排序稳定的逻辑 DDL 样式 schema。每个 dataset 包含逻辑名、description、grain、primary key、fields 和仍在授权 scope 内的 relationships；每个 field 包含名称、logical type、nullable、description、meaning、classification、enum values，以及 contract 提供的 decimal、unit、currency、timestamp、identifier、serialization、reference 语义。decimal 仍标为逻辑 `DECIMAL`，不会被转换成 `FLOAT`；Contract 没有的 currency 字段不会被臆造。

完整语义以 canonical JSON 编码到 escaped SQL `COMMENT` 字符串中，使现有 `SchemaService` 可以读取 field/table metadata。schema 最大为 32,768 个字符；超过上限即失败，不截断。通用 `PromptBuilder` 把 schema 放在 `BEGIN_SCHEMA_METADATA` / `END_SCHEMA_METADATA` 标记内，并明确 schema 描述和值是数据，不是 system instructions。

## Generator port 与 Text2SQL adapter

application 层只依赖 `ManagedSQLGeneratorPort`。传给 port 的封闭 request 只有 `question`、trusted `engine` 和 renderer 生成的 `rendered_schema`。infrastructure 中的 `Text2SQLServiceManagedGenerator` 固定调用：

```text
schema_context = rendered_schema
database_name = None
use_rag = False
```

adapter 不接收任意 RAG collection、调用者 schema、物理数据库选择或凭证。它把现有 `Text2SQLResult` 转成 `ManagedSQLDraft`，保留 generic validation、token usage 和 model explanation。生成异常对外映射为不含内部异常细节的 typed error。

## AST authorization

D3 使用固定版本 `sqlglot==30.19.0`，声明在 `backend/requirements.txt`，该文件由仓库现有 Conda、Docker 和 CI 安装路径消费。SQLGlot 采用 [MIT license](https://github.com/tobymao/sqlglot/blob/main/LICENSE)。此解析依赖提供实际 AST 和 scope resolution；regex 与通用 `SQLValidator` 继续用于结构安全，不承担 dataset/column authorization。项目文档说明 SQLGlot 是 lenient parser，因此 D3 仍显式限制 root node、statement 数、source scope 和允许字段；“能 parse”本身不是授权结论。

| `SQLEngine` | SQLGlot dialect |
| --- | --- |
| `hive` | `hive` |
| `spark_sql` | `spark` |
| `mysql` | `mysql` |
| `clickhouse` | `clickhouse` |

AST gate 只接受单条 `SELECT` 或 `UNION` / `INTERSECT` / `EXCEPT` query。拒绝 DDL/DML、command/procedure、`INTO`、lock、table function、已知外部数据源函数、未知/UDF function、physical database/catalog qualifier 和不在 effective catalog 的 base dataset。CTE、nested subquery 和 set-operation 每个 branch 都继续解析并检查其 base table scope；CTE 名称不会被当成获准的外部 dataset。

SQLGlot schema-aware column qualification 用 effective catalog 的字段映射校验每个 scope 中的列。未知字段、无法解析或 ambiguous 的列均拒绝。只允许逻辑 dataset 名，例如 `orders_v1`；`prod.orders_v1`、`database.orders_v1` 与 `catalog.schema.orders_v1` 均拒绝。拒绝 `*` 和 `alias.*`，只允许直接 `COUNT(*)` 聚合。

## LIMIT、大小与计划

policy 默认最多 100 行，受 generic Text2SQL 上限 500 行约束。candidate 先通过 AST read-only/catalog 检查，再由既有 `SQLValidator.enforce_row_limit()` 添加/限制通用 500 行上限，随后按 managed policy 再限制；较小的 candidate LIMIT 保留。最终 SQL 再经过 `SQLValidator.validate()` 与 AST/max-row 检查。支持的 engine 写法按 parser 和测试结果验证，包括 MySQL `LIMIT offset,count`。SQL 最大为 16,384 个字符；超过 schema/SQL 上限均 fail closed，不做截断。

`ManagedBusinessSQLPlan` 包含 request/tenant metadata、contract identity/version、catalog fingerprint、engine、effective datasets、AST 确认的 referenced datasets/fields、最终 SQL、row/classification policy、token usage、验证摘要及 `READY` 状态。该模型没有 `EXECUTED` / 成功查询状态，也没有 result rows。

tenant identity 留在 trusted plan metadata 中，不写入 SQL 字符串。D3 并没有证明 tenant 到 warehouse row-level security 的映射，也没有实现 SQL 中 tenant 谓词、tenant authentication 或 RLS。catalog scope 是本次允许使用的逻辑 schema 范围，不等同于行级隔离。

## 当前边界与 D4

D3 不执行 SQL，不建立数据库/warehouse 连接，不读取 SupportOps Delivery manifest 或 JSONL，不做 ingestion，不调用 SupportOps runtime，不接生产 Agent、FastAPI Business Analytics API 或 Streamlit，也不提供 tenant authentication、RLS、EXPLAIN/dry-run 或 query-result verification。

D4 已在独立阶段实现受治理的内部 snapshot 执行；D3 仍只负责生成并授权逻辑 SQL plan。D4 具体执行边界见 [GOVERNED_BUSINESS_ANALYTICS_EXECUTION.md](GOVERNED_BUSINESS_ANALYTICS_EXECUTION.md)。

## 验证范围

测试使用真实 pinned Contract catalog、真实 D1 workflow、真实 renderer、真实 SQLGlot authorizer 与 fake generator；不发起真实模型、网络或数据库调用。测试覆盖 deterministic rendering、classification filter、scope、column resolution、ambiguous fields、physical qualifier、wildcard、read-only syntax、CTE/subquery/UNION、外部函数、四种 dialect、LIMIT clamp、size limits、Text2SQL adapter 参数和架构边界。

## D4 update

D3 remains the logical SQL candidate and source-dialect authorization stage. D4 consumes only a `READY` plan, rechecks its effective catalog against the pinned D2 catalog, validates Delivery v2, materializes the effective scope, translates and reauthorizes the SQL, then executes it under separate D4 budgets. The D3 plan carries the exact Contract SHA and an internal effective-catalog reference excluded from serialization. See [GOVERNED_BUSINESS_ANALYTICS_EXECUTION.md](GOVERNED_BUSINESS_ANALYTICS_EXECUTION.md) for execution and result boundaries.

## D5 update

D5 adds an authenticated principal and server-issued tenant grant ahead of this pipeline. Managed API/Agent callers still provide only question, engine, and optional requested datasets; D1-D3 enforce the effective catalog scope and no model-provided identity reaches this stage. Generic Text2SQL remains independent. See [TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md](TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md).
