# Business Data Delivery v1

## Version boundaries

Business Data Contract `v1` defines business datasets, fields, classifications, and
compatibility. Delivery Export Format `v2` defines the local directory, manifest,
canonical JSONL, snapshot metadata, and validator protocol. Format v2 is intentional:
the required `snapshot` manifest object is not compatible with the previous v1
packaging shape. The Contract remains v1.

SupportOps produces the delivery; future consumers validate and consume it. A consumer
must depend on the Contract, delivery protocol, and validator semantics only. It must
not import SupportOps ORM models, Store, migrations, internal tables, or orchestrator.

## Snapshot semantics

The exporter reads all four datasets through one explicit connection and transaction.
For PostgreSQL it uses `REPEATABLE READ` followed by `SET TRANSACTION READ ONLY`; all
four SELECT statements therefore observe one PostgreSQL MVCC snapshot without a global
lock. The repository includes an isolated PostgreSQL integration test for this behavior.

SQLite uses one connection and an explicit `BEGIN` read transaction. It provides the
strongest local reference behavior used here, but is not represented as PostgreSQL
MVCC/repeatable-read equivalence. SQLite lock/WAL configuration and concurrent writer
behavior remain deployment-specific.

Format v2 manifest snapshot metadata contains:

- `consistency`: the actual exporter transaction mode.
- `started_at` and `completed_at`: snapshot-read timing, not business watermarks.
- `snapshot_id`: a deterministic SHA-256 over contract hash/version, tenant, currency,
  ordered dataset hashes, and row counts. It identifies delivered content, not a DB
  transaction ID, LSN, or random invocation UUID.
- `watermarks`: per-dataset business-progress metadata.

Orders have no reliable source change timestamp and actions have no reliable lifecycle
update timestamp, so both declare `kind: unavailable`. Tickets expose the maximum
`updated_at`; ticket events expose the maximum `created_at`. Empty timestamp datasets
use `max_value: null`. `generated_at` is artifact creation time and is never a
watermark.

## Delivery identity and idempotency

The standalone validator returns `delivery_fingerprint`: SHA-256 of canonical
`manifest.json` bytes, a newline, and the ordered descriptor content hashes. Consumers
should validate the entire delivery first, then use this fingerprint as a content-based
idempotency key. `snapshot_id` identifies the semantic data snapshot; the fingerprint
also includes packaging metadata such as generated time. Neither implies a consumer
storage implementation.

## Offline validator

Run the validator without a database, authentication, LLM, or DataCopilot:

```powershell
D:\Anaconda3\envs\supportops-ai\python.exe scripts/validate_business_data_v1.py `
  --delivery .\delivery-v2
```

It validates canonical manifest bytes, contract and format versions, tenant/currency,
contract hash, snapshot metadata, expected descriptors, safe basenames, regular files,
UTF-8, no BOM, nonblank canonical JSONL records, hashes, row counts, exact field sets,
nullability, primary keys, enums, decimal/timestamp forms, tenant consistency, and
relationships that exist within the delivery.

It rejects unknown directory entries, duplicate datasets/files/JSON keys, absolute or
traversal paths, symlinked manifest/datasets, malformed or oversized manifest files,
and records exceeding the one MiB line bound. It streams datasets line by line; memory
is O(unique primary keys retained for relationship and duplicate checks), rather than
O(full JSONL bytes). It is a v1 reference validator, not an unlimited-data platform.

## Compatibility gate

`compatibility_baseline.json` is a compact frozen structural surface, not a duplicate
Contract. It records dataset names, grain, primary keys, classifications, and field
name/type/nullability/classification/enum values. The gate rejects dataset removal or
addition, grain/key/type/nullability/classification/enum changes, removed fields, and
new required fields. New optional fields are allowed.

This gate cannot prove that a prose description or semantic meaning remains equivalent.
Such business semantic changes still require explicit code review and, where needed, a
new Contract major version.

## Authority and limits

`tenant_id` is a stored SupportOps fact and is filtered at export. `currency` remains
trusted delivery partition metadata, not a record-level observed fact; validation can
check its three-uppercase-letter format but cannot prove source-money currency.
Persistence money remains Float, so decimal rendering cannot restore lost precision.
SupportOps has no RLS.

The producer does not implement retention, archival, replay storage, scheduling,
consumer intake, or deduplication storage. Those are future consumer/platform policy.
No DataCopilot integration exists in this phase.

