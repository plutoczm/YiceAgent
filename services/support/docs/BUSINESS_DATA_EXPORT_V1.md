# Business Data Export v1 and Delivery Format v2

## Purpose

Business Data Export v1 turns the SupportOps-owned Business Data Contract v1 into a
machine-readable Delivery Format v2 artifact. It reads current SupportOps persistence and writes a
delivery directory containing `manifest.json`, `orders_v1.jsonl`, `tickets_v1.jsonl`,
`ticket_events_v1.jsonl`, and `actions_v1.jsonl`.

This is a read model producer, not an ORM dump. It explicitly selects and maps only
the four Contract v1 datasets. It does not create views, mutate business state, call
the orchestrator, seed data, or connect DataCopilot.

## Running the exporter

From the repository root, use the existing environment and database configuration:

```powershell
D:\Anaconda3\envs\supportops-ai\python.exe scripts/export_business_data_v1.py `
  --output .\delivery-v2 `
  --tenant-id TENANT-DEMO `
  --currency USD
```

The optional `--generated-at` accepts an RFC 3339 timestamp with an explicit timezone
for reproducible artifacts. The output directory must not already exist; the exporter
fails closed instead of overwriting it. The CLI prints only contract version, tenant,
currency, row counts, and output path. It never prints records, a DSN, or secrets.

## Tenant and currency context

`tenant_id` is a real SupportOps persistence fact. The exporter requires a validated
tenant in `DeliveryContext`, applies it as a predicate to every query, and emits the
stored row tenant. It does not invent tenant IDs or provide row-level tenant isolation
itself; database schema, trusted authentication, and Store predicates already
establish that ownership boundary.

`currency` is different: current persistence has no per-record currency field.
`DeliveryContext.currency` is a trusted delivery context, written to the manifest for
the whole delivery. It is validated only as three uppercase ASCII letters (`[A-Z]{3}`).
That is ISO-4217-shaped format validation, not an ISO registry. Consumers must not
infer currency, aggregate across unknown/different delivery currencies, or convert
values.

## Mapping and canonical values

The exporter uses four explicit SQLAlchemy Core `SELECT` mappings:

- `orders_v1`: stored order fields; `amount` and `refundable_amount` are converted.
- `tickets_v1`: stored ticket fields except `reason`.
- `ticket_events_v1`: stored transition fields except `note`.
- `actions_v1`: selected durable operation scalars. `kind` and `status` pass through
  explicit mapping tables to `action_kind` and `action_status`; payloads, results,
  snapshots, receipts, approval snapshots, and actor confirmation data are excluded.

Rows are ordered by their stable business key: `order_id`, `ticket_id`,
`created_at + event_id`, and `action_id`, respectively. JSON Lines use UTF-8, one
newline per record, sorted JSON keys, compact separators, explicit `null`, and
`allow_nan=False`.

Contract monetary values are canonical decimal strings. A persisted finite `Float` is
converted with `Decimal(str(value))`, rendered in fixed-point form, and stripped of
unneeded trailing fraction zeros. This prevents binary-float JSON rendering and
exponent notation, but cannot recover precision already lost when persistence stored a
float. NaN and infinity fail the export.

Timestamps are emitted as RFC 3339 UTC values ending in `Z`. Timezone-aware values
are converted to UTC. Existing SQLite persistence can return a naive `DateTime`;
SupportOps treats that stored representation as UTC before rendering, so delivered
data never mixes naive values or varying offsets.

## Manifest and integrity

`manifest.json` contains contract name/version, Delivery Format v2, generated time,
tenant, currency, contract-file SHA-256, encoding, media type, snapshot metadata, and
ordered dataset descriptors. Each descriptor includes dataset name, file name, row count,
and the SHA-256 of the exact UTF-8 JSONL bytes. The exporter computes the hash after
writing the dataset content and validates counts and hashes before publishing the manifest.
See `docs/BUSINESS_DATA_DELIVERY_V1.md` for snapshot, watermark, validator, and consumer
identity semantics.

## Contract validation and sensitive data

Before writing, each mapped record is checked against `contract.json`: it must have
exactly the declared fields; satisfy nullability, primary-key uniqueness/non-emptiness,
enum values, decimal format, timestamp format, and boolean type. This makes Contract
v1 the validation source of truth rather than the persistence schema.

The mapping and tests explicitly exclude free text, opaque JSON, audit details, message
body/context, prompt/model content, traces, and infrastructure metadata. A future ORM
column is not exported unless a future contract and explicit mapping add it.

## Read-only and delivery publication

The exporter opens an Engine connection and issues only Core `SELECT` statements. It
has no business mutation calls, ORM writes, audit writes, schema creation, seed, or
commit. Tests snapshot orders, tickets, events, and actions before and after export.

Files are written to a temporary sibling directory. Only after all datasets are
validated, hashed, and the manifest is validated is that directory renamed to the
final destination. If the destination already exists, export fails before any write.
A process failure removes the temporary directory; a consumer never sees a normal
final directory until the delivery is complete.

## Current limits

This implements a local filesystem delivery only. It does not provide cloud storage,
cross-filesystem atomicity, a currency registry, per-record currency provenance, an
incremental watermark, an export scheduler, a DataCopilot consumer, or a payment/return
settlement ledger. Future consumers must depend on the Contract plus a completed
delivery, not SupportOps ORM classes.
