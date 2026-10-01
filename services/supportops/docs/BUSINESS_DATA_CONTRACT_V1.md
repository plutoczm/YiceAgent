# Business Data Contract v1

## Purpose and boundary

`contracts/business_data/v1/contract.json` is the SupportOps-owned,
machine-readable business interface for approved external analytical consumers. It is
a versioned semantic boundary, not a serialization of SQLAlchemy models or a database
schema. It defines only `orders_v1`, `tickets_v1`, `ticket_events_v1`, and
`actions_v1`.

This S3.1 change does not create database views, an export process, a DataCopilot
consumer, a cross-repository import, or a schema registry. SupportOps persistence can
evolve independently as long as a future producer preserves this contract's semantics.

The logical primary key for every dataset starts with `tenant_id`. This is deliberately
a business identity and partitioning rule; it does not mirror the physical ORM primary
key. Consumers must preserve tenant isolation for every join, filter, and delivery.

## Dataset catalogue

| Dataset | Grain | Exposed fields |
| --- | --- | --- |
| `orders_v1` | One current order per tenant | `tenant_id`, `order_id`, `customer_id`, `status`, `amount`, `refundable_amount`, `refund_status`, `return_status` |
| `tickets_v1` | One current support ticket per tenant | `tenant_id`, `ticket_id`, `customer_id`, `conversation_id`, `status`, `priority`, `assignee_id`, `sla_due_at`, `created_at`, `updated_at` |
| `ticket_events_v1` | One recorded ticket status transition per tenant | `tenant_id`, `event_id`, `ticket_id`, `actor_id`, `from_status`, `to_status`, `created_at` |
| `actions_v1` | One current durable refund/return-request lifecycle per tenant | `tenant_id`, `action_id`, `order_id`, `ticket_id`, `conversation_id`, `customer_id`, `action_kind`, `action_status`, `quoted_amount`, `approval_required`, `created_at`, `expires_at`, `confirmed_at`, `terminal_at` |

`ticket_events_v1.ticket_id` references `tickets_v1` in the same tenant. When
present, `actions_v1.order_id` references `orders_v1`, and `actions_v1.ticket_id`
references `tickets_v1`, also in the same tenant. Null `actions_v1.order_id` is a
known migrated-legacy limitation; new operations are order-scoped.

## Business semantics

`orders_v1` represents the producer's current operational order facts. Its `status`,
`refund_status`, and `return_status` are intentionally strings rather than a frozen
enum: the current source does not enforce an exhaustive order-status enumeration.
`refund_status` is a local operational state, not evidence of payment-provider
processing, reconciliation, or settlement. `return_status` is likewise not evidence
of physical receipt, inspection, or completion.

`tickets_v1` is the current ticket record, while `ticket_events_v1` is the transition
history. The ticket lifecycle enum is `open`, `assigned`, `pending_customer`,
`resolved`, and `closed`; its valid transitions remain a SupportOps-owned lifecycle
rule. The event dataset does not publish its free-text note.

`actions_v1` represents a requested business action and its current locally durable
lifecycle. It is not a completed external mutation fact. In particular, `submitted`
means SupportOps recorded a local submission after its governed workflow; it does not
mean that a payment settled or a return completed. The only action kinds are `refund`
and `return_request`. `quoted_amount` is present only for refunds; no opaque action
payload, result, snapshot, receipt, approval snapshot, or internal version/hash is
published.

All timestamps are UTC instants and must be serialized as RFC 3339 values with an
explicit offset. They are event/record times as defined for each field, not implicit
local times.

## Monetary values

`amount`, `refundable_amount`, and `quoted_amount` have the contract logical type
`decimal`, use major currency units, and are serialized as canonical base-10 decimal
strings without exponent notation. This is intentional even though current persistence
uses `Float`: persistence representation is not the business data type.

The current SupportOps source has no per-record currency code. Therefore a v1 delivery
must declare one known currency context for its delivered partition before consumers use
monetary values. Consumers must not silently infer currency, aggregate across unknown or
different currencies, or perform a conversion. Adding a per-record currency field is a
future contract change only when an authoritative source exists.

## Classification and exclusions

The contract uses four classifications:

| Classification | Rule |
| --- | --- |
| `public` | Unrestricted disclosure is approved. No v1 field is public by default. |
| `internal` | Operational business data for approved internal consumers. |
| `confidential` | Identifiers and facts that can identify, correlate, or profile a tenant, customer, conversation, actor, order, ticket, or action. Tenant-scoped access is required. |
| `restricted` | Free text, opaque JSON, detailed audit content, prompts, model responses, secrets, traces, and infrastructure metadata. It is outside this contract. |

The following are expressly excluded: `tickets.reason`, `ticket_events.note`,
`ticket_messages.body`, `ticket_messages.context`, `audit_events.details`, all raw
`pending_actions` payload/result/snapshot data, receipt result data, prompt/response
content, trace identifiers, authentication secrets, and infrastructure metadata. They
are either free text, opaque JSON, internal implementation data, or too sensitive for
the first analytical interface.

## Compatibility rules

Within `v1.x`, SupportOps may add an optional field, add dataset metadata, or clarify
a non-breaking description. It must not silently remove a field, change a field's
business meaning, change a dataset's grain or primary-key semantics, make an optional
field required, make an incompatible logical-type change, or change the meaning of an
enum value. Any such breaking change requires a new major contract version.

The contract test validates its static structure only. It intentionally does not query
a database, generate an ORM schema, call another service, or claim that an export
producer exists. A future S3.2 can add a separately governed producer and delivery
mechanism while preserving this boundary.

