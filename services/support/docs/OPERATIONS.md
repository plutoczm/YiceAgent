# S2b: durable business operations

SUBMITTED means a **locally recorded refund/return request**. It is not payment
settlement or physical return completion. No payment provider, outbox, financial
ledger, distributed exactly-once claim, Agent runtime or Business Data Contract is added.

## Identity and deduplication

The existing `pending_actions` table is the canonical operation, coordinated by
`BusinessOperations` in `app/operations.py`. Persisted `action_id` is exposed as
`operation_id`; the ACT prefix and old confirmation URL remain compatible.
Business identity is not a trace, HTTP request or model tool-call ID.

Current whole-order business key:
`tenant_id + customer_id + order_id + kind(refund / return_request)`.

A partial unique index covers non-legacy PENDING_CONFIRMATION, PENDING_APPROVAL
and SUBMITTED rows. Repeated preparation reuses the active operation; an already
submitted operation replays its durable result. New preparation after CANCELLED,
EXPIRED, REJECTED or INVALIDATED may create a new ID if currently eligible and no
local request has already been recorded. Old confirmation/approval never transfers.

The first conversation reference is retained. Chat messages can still be recorded
on repeated requests; business deduplication does not deduplicate conversation
history. Resource IDs remain globally unique as in S2a. Partial/item refunds and
another financial payout for the same submitted whole-order intent are unsupported.

## Lifecycle

| State | Action/condition | Result |
| --- | --- | --- |
| New normal refund / delivered return | Prepare | PENDING_CONFIRMATION |
| New high-value refund | Prepare | PENDING_APPROVAL + linked HIGH ticket |
| PENDING_CONFIRMATION | Customer confirms valid proposal with required approval | SUBMITTED |
| PENDING_APPROVAL | Customer confirms first | Stay PENDING_APPROVAL; persist confirmation |
| PENDING_APPROVAL | Employee approves first | PENDING_CONFIRMATION |
| PENDING_APPROVAL | Employee approves after customer confirmation | SUBMITTED |
| PENDING_APPROVAL | Employee rejects | REJECTED |
| Either active state | Customer cancels | CANCELLED |
| Either active state | Deadline passed | EXPIRED |
| Either active state | Relevant authority changes | INVALIDATED |
| Terminal | Repeat customer confirm/cancel | Same state/result; no new mutation |

Same-state transitions record an independent confirmation/approval fact. There is
no EXECUTING state because local submission is atomic. Expiry is materialized on
resolution, approval, repeated preparation or tenant-scoped `expire_due`; no
scheduler is installed. GET returns persisted state plus deadline.

## Durable authority

Customer confirmation stores subject, time and exact snapshot hash on the scoped
operation. It is never inferred from model output. `confirm` is required and strictly
boolean: empty JSON, null, strings and integers yield HTTP 422.

High-risk approval additionally requires a **JWT-authenticated support_admin**.
Ordinary support_agent, support:read/support:write, and dev-header admin identity
cannot approve. `operation_approvals` stores one accepted decision: tenant, operation,
approve/reject, actor, timestamp, state revision at decision, snapshot version/hash
and content. Ticket status/text is never approval authority.

First committed decision wins. Same-decision replay preserves the original actor
and time; opposite decisions conflict with HTTP 409. A new decision requires an
expected state revision. Customer confirmation can change that revision, requiring
the approver to refresh. Public confirmation/approval flags describe recorded facts;
terminal state and current snapshot validation still determine executability.

The immutable snapshot contains tenant/customer/order/kind, amount, the actual refund
threshold, approval requirement and the delivered requirement for returns. SHA-256
identifies canonical snapshot content. No formal policy version is invented.
`snapshot_version=1` is a representation version; separate `version` increments
on state changes. Changed proposals use new operation IDs, not edited snapshots.

Confirmation/submission revalidates the locked authoritative order, operation and
approval. Amount increases **and decreases**, lost eligibility/ownership, policy
changes and mismatched authority invalidate durably. Restoring source data does not
revive an invalidated operation. Existing float amounts remain; no currency or
unsupported quantity/provider rules are introduced.

## Transactions, concurrency and recovery

All mutation paths lock **order then operation** with SELECT FOR UPDATE. Existing
operation state is refreshed after waiting, so ORM-cached pre-lock state is not used.
Tenant/customer/operation/version predicates guard UPDATEs. Parent locking serializes
ordinary competitors; the partial unique index also rejects uncoordinated duplicate
inserts. Approval uniqueness prevents contradictory decision rows.

One successful submission transaction contains any new confirmation/approval,
order requested state, receipt, required business audit and SUBMITTED operation
state/result. High-risk preparation also creates/links its ticket in the same
transaction, reusing TicketWorkflow with a caller-owned session.

Faults before order write, after order write, after receipt, in audit, or after
terminal update roll back the entire command. Earlier committed customer consent
survives a failed later approval, but that new approval and submission roll back.
Commit followed by a lost response is recovered by same-operation replay.
A submitted operation with missing/inconsistent receipt fails closed.

Lock timeouts/conflicts roll back and propagate; no automatic WRITE retry is added.
Explicit retry reloads authoritative state. Confirm/cancel races have one terminal
winner; the other caller replays it. Approve/reject races have one accepted decision
and an opposite-decision conflict.

Redis action leases/TTL mirrors no longer gate operation resolution. Their settings
remain compatibility settings. Request rate limiting and its startup/degradation
policy remain. Redis is not the operation correctness mechanism.

SQLite enables foreign keys for fast serial tests; concurrency acceptance uses real
PostgreSQL, independent connections, barriers/events, held transactions and bounded
lock timeouts, not sleeps as the sole race synchronization.

## Database integrity

New constraints enforce canonical states, valid new identity/quote, confirmation,
submitted-result evidence, and non-executable legacy records. Composite scoped FKs
link operations to orders/tickets, approvals to operations, receipts to operation
tenant/ID/kind, and business audit to operations. Parent composite unique keys support
those FKs. Receipts are unique per operation; new receipt keys must equal operation IDs.

Existing global keys and S2a query indexes remain. No raw order mutation or independent
mark-action-executed store API remains as an alternate submission boundary.
Business audit participates in the transaction; diagnostic telemetry need not roll
back. No operation ID, amount or customer text is added as a Prometheus label.

## HTTP and MCP

| Endpoint | Purpose |
| --- | --- |
| POST /v1/actions/{id}/confirm | Existing route; explicit confirm/cancel |
| GET /v1/operations/{id} | Own-customer or same-tenant employee status |
| GET /v1/agent/tickets/{ticket_id}/operation | Linked human-work operation |
| POST /v1/agent/operations/{id}/decision | JWT admin decision + expected_version |

Public status omits approval actor/hash/internal audit. Prepare responses add
`operation_id` and `operation_status`. High-value responses retain handoff/ticket
and an empty legacy pending_action_id; clients use operation_id to confirm.
Controlled approval APIs are provided; no new approval UI or IdP setup is built.

The legacy confirmation DTO maps SUBMITTED to status=executed and additionally
reports operation_status=submitted. This means local recording only. Drift now
returns durable INVALIDATED state instead of leaving pending state with HTTP 404.
Cross-tenant/customer references remain not found; stale decisions use 409.

MCP keeps its four tools and S2a scopes. request_refund can prepare approval-pending
operations. No confirmation, approval, force-submit or raw execution MCP tool is
exposed. Models can propose/classify but cannot supply durable authorization.

## Migration

Revision `20260917_0005` follows `20260915_0004`; historical migrations are unchanged.

- Old rows are marked legacy with snapshot/version 0 and unknown approval provenance.
- Old pending proposals become INVALIDATED and require new preparation.
- Executed rows with matching well-formed local receipts retain SUBMITTED local facts.
  Unverified records remain non-executable legacy evidence.
- Cancelled/expired rows stay terminal. Compatible receipts link to operations;
  orphan/mismatched receipts remain legacy evidence without an authorization link.
- Migration audits record mappings. No historical customer/employee consent is invented;
  legacy high-value tickets do not become approved.
- Legacy rows cannot be active and are excluded from new business uniqueness.

A requested order without verified submitted-operation evidence requires manual
legacy investigation; it is not silently submitted again. Migration does not repair
uncertain historical facts.

Legacy-only/empty downgrade and re-upgrade are supported and tested. Downgrade refuses
any new S2b operation to protect consent/approval evidence. Legacy invalidated/rejected
rows map to old EXPIRED compatibility on downgrade, never to executable proposals.

## Remaining boundary and next round

No distributed exactly-once, settlement, full ledger, RLS, private tenant RAG,
AgentOpsHub integration, exporter or warehouse contract is claimed. S2a identity
and customer isolation remain. Real IdP deployment, comprehensive messaging privacy,
external payment recovery and production-scale performance remain separate work.

**Recommendation only:** Business Data Contract v1 should define tenant-scoped
operational snapshots and vocabulary for operations, decisions, local receipts,
tickets and audit; include stable IDs, state revisions, timestamps and explicit
legacy/provenance markers. Specify extraction consistency, privacy and schema
evolution with DataCopilot's analytical ownership. Do not infer settled payments,
missing currency or historical approval. The data-contract round has not started.

## Executed S2b validation

Environment: Windows, existing Conda supportops-ai, Python 3.11.15 at
`D:\Anaconda3\envs\supportops-ai\python.exe`. No dependency installation or .venv.

| Command with that interpreter | Result |
| --- | --- |
| `-m pytest -m "not integration" --cov=app --cov-report=term-missing --cov-fail-under=75` | 202 passed, 1 skipped, 24 deselected; 86.72% coverage |
| `-m pytest -m integration tests/integration/test_tenant_migration.py tests/integration/test_operation_postgres.py -s` | 23 passed on PostgreSQL 17.11; 40 existing Alembic path_separator deprecation warnings |
| `-m evals.run_evals` | 140 cases; quality metrics 1.0, unsafe mutations 0 |
| `-m evals.run_retrieval_evals` | 60 cases; reported metrics 1.0 |
| `-m evals.run_e2e_regression` | 61 scenarios; no failures |
| `-m evals.run_e2e_regression_v2` | 60 scenarios; existing gates pass, known mismatch retained |
| `-m evals.run_operation_evals` | 7 stateful JWT HTTP scenarios; no failures |
| `-m evals.run_slo_smoke` | 60 requests; zero errors/degradation, P95 39.714 ms under 750 ms |
| `-m ruff check .` | Pass |

The retained v2 miss is `v2-knowledge-shipping-02`: tool selection/task completion
0.9833, citation consistency 0.95, safety/customer isolation 1.0. Frozen scenarios
and gates were not altered. S1/S2a unit cases retain their safety assertions while
adapting the intended contracts: drift is durable INVALIDATED, terminal calls replay,
submission audit failures roll back, and Redis no longer gates database correctness.

PostgreSQL tests explicitly verify concurrent preparation, confirmation, confirm/cancel,
approve/approve and approve/reject, named uniqueness/CHECK/FK constraints, order locking,
lock-conflict rollback and explicit retry, five failure checkpoints, high-risk approval
rollback, legacy mapping, drift/current/head and guarded downgrade/re-upgrade.

These are local deterministic regression and isolated PostgreSQL results. They do
not establish production throughput, real IdP deployment, Redis/Qdrant HA or external
financial execution. CI is wired to run these contracts; no remote CI run is claimed.
