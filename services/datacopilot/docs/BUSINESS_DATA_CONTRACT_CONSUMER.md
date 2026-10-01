# Business Data Contract Consumer（D2）

## 职责与依赖方向

D2 在 infrastructure 层实现 D1 的 `BusinessCatalogPort`，将一个被明确接受的 Business Data Contract v1 JSON artifact 解析、校验并显式映射成 `BusinessCatalogSnapshot`。

```text
version-controlled accepted contract JSON
                  ↓
infrastructure BusinessDataContractCatalog
                  ↓ implements
application BusinessCatalogPort
                  ↓
BusinessCatalogSnapshot
```

`business_analytics` application 层仍只依赖自己的模型与 port，不读取文件，也不导入 infrastructure。Adapter 只依赖标准库、Pydantic 和 D1 business analytics 类型；没有 SupportOps repo 或运行时代码依赖，也没有接入 Agent、API 或 Streamlit。

## Accepted artifact 与 pin

仓库内接受的 artifact 位于：

- `contracts/external/business_data/v1/contract.json`
- `contracts/external/business_data/v1/acceptance.json`

Contract 来源是 SupportOps Business Data Contract v1。开发时比较了 SupportOps 工作树中的源 JSON 与 DataCopilot 接受文件的原始 bytes，二者完全一致，SHA-256 为 `7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b`。DataCopilot 构建和运行只需要仓库内的 accepted artifact；测试没有 sibling repository 依赖。

`acceptance.json` 不是第二份 Contract，只保存接受身份与 hash：

```json
{
  "contract_name": "supportops_business_data",
  "accepted_version": "v1",
  "contract_sha256": "<64 lowercase hex characters>",
  "source_owner": "SupportOps",
  "compatibility_policy": "exact-artifact-sha256"
}
```

Adapter 计算 Contract 文件原始 bytes 的 SHA-256，必须与 acceptance metadata 完全相等，然后才解析 JSON。名称、版本、owner 也必须与 pin 一致。D2 接受的是 exact v1 artifact；不会自动回退到 `latest`，也不会仅凭 `v1.x` 标签自动接受兼容变更。未来更新时，先审阅 producer Contract，再同步 artifact、更新 hash 并运行 consumer 回归测试。

## 文件读取与 strict parser

Adapter 的路径来自可信应用装配或 `BusinessDataContractCatalog.from_repository_root(...)`，不能来自 request、Agent 参数或 LLM 输出。读取边界要求：

- Artifact 是 regular file，拒绝 symlink，并对读取大小设上限（Contract 256 KiB，acceptance metadata 16 KiB）。
- 两个文件都必须是无 BOM 的 UTF-8。
- JSON 使用 `object_pairs_hook` 检测并拒绝任意层级的重复 key，也拒绝 Python JSON parser 接受的非标准常量。
- External Pydantic DTO 使用 `extra="forbid"`，字段类型和结构固定；缺字段、未知字段、无效枚举或非法引用都 fail closed。
- 错误映射为 typed `BusinessAnalyticsError` 子类；不会返回绝对路径、原始 JSON、schema 内容或 parser 异常文本。

DTO 校验 top-level identity、owner、description、classification definitions、compatibility metadata 和 datasets；逐 dataset 校验 grain、复合 primary key、sensitivity、stability、fields 与 relationships。它也校验 dataset/field 名称唯一、primary key 字段存在、关系目标及复合目标键有效、relationship source 字段存在且 nullable 一致、field references 符合目标 primary key，以及 enum metadata 不缺失、不重复。

## Producer DTO 与 consumer model 显式映射

Infrastructure DTO 只代表外部 v1 文件格式。Adapter 显式映射为 DataCopilot 自己的 immutable、typed catalog 模型，不直接用 `BusinessCatalogSnapshot(**raw_contract)`，也不复制 SupportOps 的 Python model。

- Dataset 保留 logical name、description、grain、复合 primary key、dataset sensitivity 和 stability。
- Field 保留 description 与 semantic meaning、logical type、nullable、classification；enum values 保持 Contract 中的顺序。decimal 的 unit、serialization、currency semantics，timestamp semantics，identifier semantics 和 references 使用 typed optional 字段，不塞入万能 dict。四类 classification 的 Contract 定义文本也作为 typed definitions 保留在 snapshot。
- `identifier`、`enum`、`decimal`、`timestamp`、`boolean` 等逻辑类型显式映射到 DataCopilot 类型。Canonical decimal 仍是 decimal 字符串语义，不转换为 float。
- `public`、`internal`、`confidential`、`restricted` 逐项映射。未知 classification 不降级到 internal/public，而是拒绝。D2 保留分类语义，不决定字段查询授权。
- Relationship 保留 source/target key、cardinality、nullable 与 semantic meaning。Contract 的 relationship source field 与 tenant partition key 组合后映射为复合 source key。

## Tenant 与 allowed dataset scope

Contract JSON 表达共享的 schema 和业务语义，不包含 tenant-specific row data。Contract 中的 `tenant_id` 是逻辑字段名，不是某个真实 tenant 的值；Contract owner 也不能证明调用方身份。

Snapshot 的 `tenant_id` 直接绑定 `BusinessAnalyticsContext.tenant_id`。`user_id`、文件路径和 Contract 内容都不用于推导 tenant。Fingerprint 表示 schema/effective scope identity，不表示 tenant data identity。

Adapter 在映射前检查所有 `context.allowed_datasets` 都存在于 Contract；未知名称抛出 `BusinessScopeViolationError`，不会静默丢弃。只映射允许的 datasets。Relationship 仅在 source dataset 和 target dataset 都获允许时进入 snapshot；field reference 的 target 不在允许范围内时设为 absent。这样不会返回未授权 dataset 的字段/说明，也不会留下悬空关系。D1 `BusinessAnalyticsWorkflow.prepare()` 的 scope 检查继续保留，作为第二道一致性检查。

## Catalog fingerprint

定义如下：

```text
scope_text = "\n".join(sorted(allowed_datasets))
catalog_fingerprint = SHA256(contract_sha256 + "\n" + scope_text).hexdigest()
```

因此同一个 accepted Contract 与相同 scope 顺序无关，scope 不同则 fingerprint 不同。Fingerprint 不包含 `tenant_id`、`request_id`、时间戳或 filesystem path；不同 tenant 使用相同 schema/scope 时 fingerprint 相同，tenant identity 仍单独保存在 snapshot。

## D2 不做的事与 D3

D2 只读取 version-controlled Contract 与 acceptance metadata。它不读取 `manifest.json`、Delivery JSONL 或 row data，不复制 producer validator，不访问网络/数据库/LLM，不渲染 `schema_context`，不调用 Text2SQL/SQL Review，不生成或执行 SQL，也未接入 production API/Agent。

D3 才会从 trusted snapshot 选择/渲染 schema，并构建 deterministic managed Text2SQL pipeline。Generic Text2SQL 的手工 `schema_context` 路径继续独立存在，不因此获得 enterprise authorization。

## D3 update

D3 consumes the typed snapshot returned by the existing D2 contract catalog through D1's `BusinessCatalogPort`. It applies a server-controlled field classification policy and renders only the resolved datasets and permitted fields. The adapter still reads only the accepted, pinned contract and acceptance metadata; D3 does not read SupportOps manifests or delivery JSONL. See [MANAGED_TEXT2SQL_PIPELINE.md](MANAGED_TEXT2SQL_PIPELINE.md) for the SQL candidate and authorization boundary.

## D4 update

D4 uses the same pinned D2 artifact metadata and adds a separate DataCopilot consumer for Delivery Format v2. It validates the full four-dataset delivery, then materializes only D3's effective dataset/field scope into the isolated execution snapshot. Contract v1 remains distinct from Delivery Format v2. No SupportOps validator code or runtime is imported. See [GOVERNED_BUSINESS_ANALYTICS_EXECUTION.md](GOVERNED_BUSINESS_ANALYTICS_EXECUTION.md).

## D5 update

D5 builds authenticated grants from server configuration and validates each grant's Contract version and dataset names against this accepted artifact. Request JSON and Agent tool arguments cannot alter the grant or Delivery directory. The identity integration and deployment configuration limits are documented in [TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md](TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md).
