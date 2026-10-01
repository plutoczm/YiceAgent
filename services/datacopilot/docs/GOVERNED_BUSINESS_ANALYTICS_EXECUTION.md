# Governed Business Analytics Execution (D4)

## Goal and status

D4 adds a local, internal execution workflow on top of D1 trusted context, the D2 pinned Contract catalog and D3 authorized SQL plan. The result status is `EXECUTED` only after a bounded query finishes and a complete result is collected. It does not represent durable storage, production identity, RLS, or a deployed analytics service.

```text
BusinessAnalyticsRequest + trusted BusinessAnalyticsContext
    -> D1 BusinessAnalyticsWorkflow.prepare
    -> D2 pinned full Contract metadata + scoped catalog
    -> D3 ManagedBusinessSQLPipeline -> READY plan
    -> exact trusted tenant-to-delivery mapping
    -> DataCopilot-owned Delivery v2 validation
    -> tenant-checked immutable metadata snapshot
    -> authorized dataset/field materialization
    -> DuckDB :memory: engine with external access disabled
    -> source SQL translation and translated SQL reauthorization
    -> real interrupt deadline + bounded fetch
    -> metadata-only audit + GovernedBusinessAnalyticsResult(EXECUTED)
```

`GovernedBusinessAnalyticsWorkflow.run(request, context)` is an internal application entry point. It does not accept a caller-supplied SQL plan, filesystem path, or engine configuration. Its only execution dependency is `GovernedBusinessSQLExecutorPort`; concrete DuckDB and filesystem classes stay under `infrastructure`.

## Trusted delivery selection

`FileBusinessDataDeliveryResolver` receives an explicit server-composition mapping from a trusted tenant identity to one exact Delivery v2 directory. It never derives a path with `root / tenant_id`, scans for the newest directory, or accepts a path from request, agent, model, or SQL text. A missing mapping fails closed. If a tenant has several deliveries, composition selects the exact accepted directory; there is no implicit filesystem-time policy.

The full Contract schema and SHA-256 come from DataCopilot's existing `BusinessDataContractCatalog.load_accepted_contract()` API. The resolver path is held only by a private infrastructure handle. The typed `BusinessDataSnapshot`, execution result, audit event, and request do not contain it.

## Delivery v2 consumer validation

The consumer validates the pinned Contract v1 and Delivery Format v2 independently; it does not import SupportOps runtime or producer validator code. It requires canonical UTF-8 manifest JSON, rejects duplicate keys and a BOM, checks the exact v2 manifest and descriptor fields/order, verifies Contract name/version/hash, trusted tenant, currency format, timestamps, snapshot consistency, row counts, file basenames, and lowercase SHA-256 values.

Each directory must contain exactly `manifest.json` and the four expected files. Files must be regular, non-symlink files directly inside the resolved mapped directory. Absolute paths, separators, traversal names, duplicate dataset/file descriptors, extra entries, oversize manifest/records, invalid UTF-8, blank lines, noncanonical JSONL, unknown/missing fields, bad nullability/types/enums/decimals/timestamps, duplicate primary keys, broken relationships, and any row whose `tenant_id` differs from trusted context are rejected.

The consumer recomputes the producer's documented `snapshot_id` material and `delivery_fingerprint` algorithm using its own code. The snapshot ID identifies content by Contract hash/version, tenant, currency, and ordered dataset names/hashes/counts. The delivery fingerprint hashes the canonical manifest bytes plus a newline and the ordered content hashes. Neither value proves who created or transported the files. No producer signature, authenticated object-store identity, or transport authentication is present, so these hashes are integrity and consistency evidence only.

Delivery Format v2 watermarks are checked against streamed rows: `orders_v1` and `actions_v1` are unavailable; `tickets_v1` uses max `updated_at`; `ticket_events_v1` uses max `created_at`. `generated_at` is artifact creation time, not a business watermark. Currency is trusted delivery-partition metadata and is never added as a SQL column.

## Two-pass streaming and materialization

Pass one streams every Contract dataset and verifies exact bytes, row/record limits, content hash, row count, field types, tenant binding, primary keys, relationships, watermarks, and per-decimal precision/scale. The bounded validator keeps unique primary keys for relationship and duplicate checks; it does not load JSONL files or raw dictionaries wholesale into DuckDB.

Pass two rereads only D3 effective datasets and rechecks each line while inserting validated typed parameters in batches. It repeats content-hash and row-count checks inside a DuckDB transaction; any mismatch rolls back before the query runs. Only fields present in D3's effective catalog become columns. Tenant identity and all Contract fields are still checked during validation even when policy excludes them from the SQL tables.

Default input policy is 1 MiB per manifest, 1 MiB per JSONL record, 64 MiB total delivery bytes, 100,000 rows per dataset, and 200,000 rows total. Materialization batches contain at most 500 rows. Work is O(total delivery bytes read); extra Python memory is bounded by one record, a batch, unique primary keys up to the row budget, and the engine/result budgets.

`ManagedAnalyticsPolicy` remains the D3 policy for visible fields/classifications, rendered schema/SQL size, and plan LIMIT. `GovernedExecutionPolicy` is separate and server-controlled; it bounds Delivery input, materialization, DuckDB memory/threads/deadline, and result rows/columns/bytes. Request fields cannot alter either policy.

## Tenant physical binding and RLS boundary

Before any SQL runs, the manifest tenant and every row tenant must equal `BusinessAnalyticsContext.tenant_id`. Each workflow creates a fresh `:memory:` database and inserts rows from only the exact mapped delivery. The engine has no other tenant's rows, and only authorized logical datasets and effective fields exist in it. This is tenant-scoped isolated snapshot execution.

DataCopilot does not claim RLS: there is no database policy engine or row-level security policy. The current trusted context is supplied by internal composition; this repository still has no production tenant identity provider. The in-memory scope does not create an authentication system.

## DuckDB choice and hardening

D4 pins stable `duckdb==1.5.5` in `backend/requirements.txt`. The repository's Conda, Docker, and CI installation paths consume that file. PyPI lists Python 3.11 wheels, Python >=3.10, and MIT licensing. DuckDB supplies in-memory analytical SQL, exact fixed-point DECIMAL, parameterized prepared inserts, `fetchmany`, external-access controls, and a Python `interrupt()` API. No optional extras are installed.

The executor opens a fresh `duckdb.connect(':memory:')` with external access disabled, a 256 MiB memory limit and one thread. Before materialization it sets `autoload_known_extensions=false`, `autoinstall_known_extensions=false`, `allow_community_extensions=false`, `TimeZone='UTC'`, and `max_temp_directory_size='0B'`, then locks configuration. Runtime tests verify these settings. Engine-level negative tests exercise file/JSON/Parquet readers, `ATTACH`, `COPY`, extension `INSTALL`/`LOAD`, and attempts to re-enable external access. DuckDB rejects those operations at the engine boundary, including attempts made without relying on D3's parser gate.

JSONL is never passed to DuckDB readers. The consumer validates bytes in Python, and materialization uses bound parameters. Tables use only exact logical Contract names. The current implementation stays in-process; it does not claim an operating-system process sandbox.

Official references: [DuckDB 1.5.5 on PyPI](https://pypi.org/project/duckdb/), [security and external access](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview), [extension controls](https://duckdb.org/docs/current/operations_manual/securing_duckdb/securing_extensions), [Python API](https://duckdb.org/docs/current/clients/python/reference/), [DECIMAL](https://duckdb.org/docs/current/sql/data_types/numeric), and [timestamp types](https://duckdb.org/docs/current/sql/data_types/timestamp.html).

## Type mapping and exact values

| Contract logical type | DuckDB type and insertion |
| --- | --- |
| identifier, string, enum | `VARCHAR`, validated Python `str` |
| boolean | `BOOLEAN`, strict Python `bool` |
| integer | `BIGINT`, strict Python `int` |
| timestamp | `TIMESTAMP`, UTC-normalized naive Python `datetime` |
| decimal | snapshot-sized `DECIMAL(precision, scale)`, Python `Decimal` |

The contract serializes timestamps as canonical UTC RFC 3339 values ending in `Z`. The consumer first parses an aware UTC instant, then converts it to a naive UTC `TIMESTAMP` because the installed DuckDB Python binding requires the optional `pytz` package for `TIMESTAMPTZ` bindings. D4 does not install that extra. Values are serialized back with an explicit `Z`; no local timezone conversion is used. This mapping is documented and tested.

During pass one, each decimal field records maximum significant integer digits and fractional scale across the snapshot. D4 selects `precision = max(1, integer_digits + scale)` and the maximum observed scale. Precision or scale above DuckDB's limit of 38 fails closed; there is no rounding or float conversion. An empty or all-null decimal field uses the explicit default `DECIMAL(38,18)`. Insert values are bound `Decimal` objects. Result decimals are serialized as base-10 strings; DuckDB may zero-pad values to the common snapshot column scale (for example `1.20` may return `1.2000`), but the exact Decimal numeric value is preserved.

Decimal division and `AVG` are excluded from D4 v1 because DuckDB uses approximate floating-point for decimal division and AVG output. D4 rejects those operators and any float/double cast before execution. Decimal overflow or unsupported result scalar types fail closed.

## SQL translation and reauthorization

D3's source SQL is revalidated with the existing structural validator and AST authorizer. SQLGlot translates with the source dialect mapping: Hive → `hive`, Spark SQL → `spark`, MySQL → `mysql`, and ClickHouse → `clickhouse`; the target is `duckdb`. Parser and unsupported-feature errors raise typed translation errors. There is no string replacement or best-effort fallback.

The translated SQL is parsed and authorized again using the same trusted effective catalog. The executor requires identical referenced dataset and field sets to D3's plan and rechecks one read-only SELECT/set-operation query, physical qualifiers, wildcard policy, functions, tables, fields, and LIMIT. A translation that expands or changes scope is rejected. The executor also reloads the pinned D2 catalog, rebuilds the policy-filtered catalog for the plan scope, and compares it to D3's internal effective catalog before use; the hidden catalog field is excluded from serialization.

D4 v1 supports projections, WHERE, GROUP BY, ORDER BY, joins, CTEs, subqueries, UNION/INTERSECT/EXCEPT, LIMIT, and a bounded built-in function set (`COUNT`, `SUM`, `MIN`, `MAX`, `COALESCE`, `NULLIF`, `LOWER`, `UPPER`, `LENGTH`, `ABS`, `CAST`). It rejects decimal division/AVG, window functions, unknown/UDF functions, and untested engine-specific date, regex, and complex-type operations. Unsupported SQL fails closed.

## Deadlines and result budgets

Each execution uses a dedicated worker thread. The worker creates and owns its DuckDB connection, materializes the trusted snapshot, and runs only reauthorized SQL. The async caller monitors a monotonic deadline; at expiry it calls `DuckDBPyConnection.interrupt()`, waits for the worker to finish and close the connection, and returns a typed timeout with no rows. Caller cancellation follows the same interrupt-and-join path. A fake controllable executor test checks orchestration; a real pinned DuckDB long query is interrupted from another thread and the test confirms `InterruptException`, connection cleanup, and worker exit.

The default deadline is five seconds, with a two-second cancellation grace. Input and query settings are server-controlled. Results allow at most 100 rows, 1 MiB of compact UTF-8 JSON for columns plus rows, 100 columns, and 64 KiB per cell. The executor reads in `fetchmany()` batches, fetches at most `max_result_rows + 1`, incrementally counts serialized bytes, and fails closed without returning partial results if any limit is exceeded. SQL LIMIT is not the only row-budget check.

Only null, string, integer, boolean, exact Decimal-as-string, date-as-ISO, and UTC timestamp-as-RFC-3339 values are returned. Float and unrecognized driver values fail closed. Result status is `EXECUTED` only after complete bounded fetching succeeds.

## Classification, disclosure, and audit

Overall result classification is the highest classification among D3-referenced source fields. For queries such as `COUNT(*)` with no referenced field, it falls back to the source dataset classification. D4 does not invent output-column lineage when it cannot prove it. D3 classification policy excludes disallowed fields before query; D4 materializes only those effective fields. This is pre-query field exclusion, not content-based DLP or role-based redaction. There is no actor/RBAC source, so the result is not described as redacted for a particular user.

`BusinessAnalyticsAuditSinkPort` receives request/tenant IDs, Contract version, catalog/delivery/query fingerprints, engine, status/error category, referenced dataset names, row count, and duration. It does not accept rows, prompts, schema, SQL text, credentials, or filesystem paths. Tests use an in-memory sink. `LoggingBusinessAnalyticsAuditSink` is a structured logger adapter, not a durable regulatory audit ledger. Success and failure paths are audited with typed safe categories.

The query fingerprint is SHA-256 over a version tag, source engine, Contract hash, catalog fingerprint, D3 row policy, and SQLGlot-canonical authorized logical SQL. It identifies the authorized logical query under that scope/policy; it is not a result fingerprint. The producer-format delivery fingerprint includes canonical manifest packaging metadata, while `snapshot_id` identifies delivered dataset content. Both, along with `generated_at` and validated watermarks, are retained for reproducibility and freshness context.

## Current boundary and limitations

D4 does not connect to live SupportOps PostgreSQL, import SupportOps runtime, implement delivery retention, or deploy any executor service. It does not add an Agent node/tool, public Business Analytics API, Streamlit page, or production tenant authentication. SHA-256 does not authenticate the producer. No RLS claim is made. Engine security controls, byte/row bounds, and same-process isolation do not replace production identity, OS-level sandboxing, durable audit retention, or transport authentication. The Windows test session could not create an on-disk symlink without elevated/developer privileges; the consumer's symlink rejection branch is tested by simulating the path's symlink status, and should also be exercised on a runner that permits actual symlink creation.

At D4 completion, the proposed next phase was D5. D5 is now implemented; see [TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md](TRUSTED_BUSINESS_ANALYTICS_INTEGRATION.md) for the authenticated API/Agent boundary, evaluation, and current deployment limits.
