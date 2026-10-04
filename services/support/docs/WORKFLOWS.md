# S1: deterministic support workflows

S2b update: [OPERATIONS.md](OPERATIONS.md) supersedes the earlier operation,
approval, transaction and replay limitations described in this phase record.
Tenant/identity and core eligibility boundaries remain; historical results below
are retained as phase evidence.

This note records the S1 baseline. S2a now adds required tenant context, scoped SQL,
production authentication and MCP write permission; see [TENANCY.md](TENANCY.md).
S1 local-request semantics remain; S2b replaces its fragmented transaction boundary.

S1 extracts the existing order/refund/return rules into `app/workflows.py`.
SupportOps owns operational business semantics; this change introduces no analytics
contract or AgentOpsHub runtime integration. Stop after S1.

## Application boundary

`HTTP / MCP -> orchestrator -> tools -> SupportWorkflows -> store / policy / TicketWorkflow`

- `CustomerContext` carries customer identity from the authenticated Principal or
  MCP server context, separately from business request parameters. It is a trusted
  Python application object, not a model-facing credential or an authentication scheme.
- `OrderLookupRequest/Result` implements an owned-order lookup. Missing and another
  customer's orders both return no order.
- `RefundEligibilityResult` provides eligibility, a stable reason code, quoted amount,
  and the current policy decision, with confirmation/review properties. No policy
  version is invented.
- `RefundPreparationRequest/Result` owns the eligibility, escalation and pending
  action branches. `ReturnPreparationRequest/Result` owns delivered-state checks
  and return preparation.
- `ConfirmActionRequest` contains a persisted action ID and explicit confirmation;
  it does not accept an order, replacement payload, amount or idempotency key.
- `RefundRequestSubmitted` and `ReturnRequestSubmitted` describe local request
  records. They are domain results, independent of HTTP response DTOs.
- `SupportTools` adapts these calls. Its former raw `execute_refund/execute_return`
  entry points are removed. Store primitives are internal
  `_record_refund_request/_record_return_request`.
- The orchestrator retains routing, message/DTO construction, the existing action
  lock, TTL failure policy, cancellation, expiration, terminal persistence and replay.
  Submission through the workflow is the business write boundary; it must run within
  the coordinator's action-resolution lifecycle. Future entry points must retain
  that coordination rather than treating the submission method as a complete lifecycle.

The workflow imports no HTTP, LLM, retrieval, router or graph runtime. A test runs
its lookup, refund and return operations in a fresh process that blocks these imports.

## Existing rules and proposal authority

Refund eligibility requires an existing owned order and a positive refundable amount.
Non-finite numbers are rejected defensively as invalid amounts. No refund window,
payment-provider state, category, currency, fraud score or quantity rules were added;
those remain missing business requirements.

`ActionPolicy.refund` is a pure threshold decision: amount **greater than** the
configured threshold requires a HIGH ticket; equality still requires customer
confirmation. High-value preparation calls the existing `TicketWorkflow.create_handoff`
and creates no pending refund action or durable approval object.

A normal pending action stores `order_id` and the quoted `amount`. That amount is
the maximum authority represented by the customer confirmation request. S1 takes
the conservative approach of requiring an exact match on revalidation:

| State at confirmation | Behavior |
| --- | --- |
| Same owned order, same positive amount, confirmation policy unchanged | Record local refund request |
| Amount increases, including 199 -> 200 or 199 -> 899 | Reject with `refund_proposal_changed` |
| Amount decreases, including 199 -> 99 | Reject with `refund_proposal_changed` |
| Amount becomes zero/negative | Reject with `order_not_refundable` |
| Order disappears or changes customer | Reject with `order_not_found` |
| Amount unchanged but current threshold now requires review | Reject with `refund_requires_new_review` |
| Quote missing, malformed or non-finite | Reject with `refund_proposal_changed` |

Revalidation reloads the persisted pending action and current order. It checks
customer ownership, PENDING status and durable expiry even for a direct internal
submission call. The store's normal write-time read also compares the prepared
amount, guarding changes between the workflow read and that read. This is **not**
a database compare-and-swap operation or a row lock.

On rejection, no new order mutation or receipt is written; the action remains
pending until cancellation/expiry. The caller must prepare a new proposal to use
changed source data. A later confirmation always revalidates again; S1 adds no
durable invalidated-proposal status. Already terminal actions preserve the existing
persisted-result replay behavior in the orchestrator.

Return preparation still requires an owned delivered order, otherwise it creates
the existing NORMAL review ticket. Confirmation rechecks ownership and delivered
state. A successful local record sets `return_status=requested`; it implies no
warehouse receipt, external authorization, shipment or completed return.

Complaint and unresolved-request escalation continue using the same TicketWorkflow
and priorities. Ticket state transitions are unchanged.

## Transaction ownership and remaining races

No schema, migration, transaction merger, lock algorithm or new persistence is added.

| Step | Owner and existing commit boundary |
| --- | --- |
| Owned-order / eligibility reads | Workflow calls store read sessions |
| Pending proposal | Workflow calls store; pending row commits independently |
| Preparation TTL + audit | Coordinator TTL call, then independent store audit writes |
| Escalation | Existing TicketWorkflow/store ticket transaction, then independent audit |
| Confirmation coordination | Orchestrator action lock and TTL; durable expiry remains authoritative |
| Local refund/return status + action receipt | Internal store method; one existing database transaction |
| Submission audit | Separate audit transaction after local mutation/receipt |
| Terminal pending-action result | Orchestrator calls store; separate transaction |
| TTL cleanup / degraded audit | Existing separate coordinator and audit operations |

An injected submission-audit failure test confirms the existing partial-commit
behavior: local request and receipt can persist while the action is still pending.
Retry with unchanged authoritative state reuses the same receipt; subsequent terminal
confirmation replays the persisted result. This test documents the current boundary,
not a guarantee of business-level exactly-once behavior.

Application revalidation does **not** solve concurrent TOCTOU. There are no row locks,
CAS/version checks, cross-request business-operation unique constraints, lease renewal,
durable approval snapshots or business-operation identity. Distinct action IDs and
changes after the last ordinary read remain S2 concerns. The existing action lock and
receipt are scoped to an action ID; they do not provide cross-request business deduplication.

## External contracts and observability

HTTP routes, DTOs and existing success messages are unchanged. Legacy action status
`executed` means that a local request was recorded; `refund_status=requested` does
**not** mean payment settlement. No payment provider is called.

The existing confirmation endpoint maps all ValueError failures to HTTP 404. S1
retains this legacy contract, including `{"detail":"refund_proposal_changed"}`;
a future API revision can give business conflicts a more accurate status.

MCP keeps its existing four tools and authentication/scope behavior.
`request_refund` uses the same preparation workflow through the orchestrator;
no raw mutation or confirmation tool is added.

Durable audit names are intentionally retained for compatibility:
`order.get`, `refund.quote`, `policy.refund`, `action.prepare`,
`refund.execute`, `return.execute`, `ticket.create`, cancellation and expiry.
Legacy `*.execute` success means local request submission only. Rejection audit
contains a reason code and timing, not new raw customer data. Preparation tool
telemetry now uses `tool.refund.prepare` / `tool.return.prepare`, and the shared
submission adapter uses `tool.action.submit`; durable audit names are unchanged.

RAG, routing rules, frozen evaluation scenarios, MCP protocol, HTTP DTOs, historical
migrations and runtime dependencies are unchanged. No autonomous agent, LangGraph,
tenant model, analytics/export pipeline or company integration is introduced.

## S1 validation (Windows, Python 3.11.15)

Interpreter: `D:\Anaconda3\envs\supportops-ai\python.exe`, existing Conda
`supportops-ai`. Local test runs explicitly select SQLite, local reliability,
deterministic retrieval and disabled external integrations/providers/OTLP export.

| Command (using the interpreter above) | Result |
| --- | --- |
| `-m pytest -m "not integration" --cov=app --cov-report=term-missing --cov-fail-under=75` | 108 passed, 1 skipped, 1 deselected; 84.49% coverage |
| `-m evals.run_evals` | 140 cases; all quality metrics 1.0, unsafe mutations 0 |
| `-m evals.run_retrieval_evals` | 60 cases; all reported metrics 1.0 |
| `-m evals.run_e2e_regression` | 61 scenarios; no failures, gates pass |
| `-m evals.run_e2e_regression_v2` | 60 scenarios; gates pass with the one known scenario mismatch |
| `-m evals.run_slo_smoke` | 60 requests, zero errors/degradation; P95 36.376 ms under 750 ms budget |
| `-m ruff check .` | Pass |

The retained mismatch is `v2-knowledge-shipping-02`: shipping-policy wording routes
to order status instead of knowledge. V2 tool selection/task completion are 0.9833,
citation consistency 0.95; confirmation, cancellation and customer isolation remain
1.0. This is regression evidence, not a new generalization claim.

The E2E and SLO harnesses now release SQLite connection pools via ExitStack before
removing their temporary directories. This fixes Windows WinError 32 without
modifying any scenario, expected result or evaluation gate.

The 44 new parametrized cases in `tests/test_s1_workflows.py` complement existing
workflow/reliability/API/complaint tests. They cover all S1 safety categories:
owned/missing/unowned orders, positive/nonpositive amounts, normal/high/threshold
preparation, amount/eligibility/ownership/policy drift, malformed quotes, explicit
confirmation, direct terminal/expired rejection, cancellation/replay, returns,
tool/orchestrator delegation, independent domain execution, HTTP and MCP compatibility,
and the unchanged partial-transaction/receipt-retry behavior.

External Redis/Qdrant/PostgreSQL/provider integration and production concurrency
were not validated in this local run.

## Exact next phase recommendations (not implemented)

- **S2a:** introduce trusted tenant context from authentication/server configuration,
  carry it through SupportOps workflows and persistence, and enforce tenant plus
  customer ownership on orders, tickets, messages, actions, receipts and audit.
  Design migration/backfill and HTTP/MCP cross-tenant negative tests before changing
  the schema; never accept tenant authority from model payloads.
- **S2b:** define persistent business-operation identity and cross-request deduplication,
  explicit transaction ownership, database uniqueness plus locking/CAS, action/operation
  replay rules and durable high-value approval snapshots/state. Bind confirmation
  and approval to operation, amount and policy conditions. Specify crash/race recovery
  before adding any external financial side effect.

S1 implements neither phase.
