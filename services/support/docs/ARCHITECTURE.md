# Architecture

## Trust, state and observability model

SupportOps AI treats model output, user-provided identifiers and external adapters as untrusted. Identity, authorization, business policy, confirmation and mutation coordination are application responsibilities. Durable business evidence and runtime telemetry are intentionally separate planes.

```text
Identity Provider
      |
verified Principal
      |
Guardrails -> Intent Router --------------------+
      |                                         |
+-----+----------------------+------------------+|
|                            |                  ||
Knowledge/RAG             read tools        mutation intent
|                            |                  |
answerability             customer scope       policy
                                                |
                                      persisted pending action
                                                |
                                      authenticated confirmation
                                                |
                                      PostgreSQL order/operation locks
                                                |
                                      re-read PostgreSQL state
                                      + expiry + customer scope
                                                |
                                      snapshot-bound business operation
                                                |
                                      atomic local request / receipt / audit / state
                                                |
                                             audit

Across the request path:
  OpenTelemetry spans -> causality / duration / exception
  Prometheus metrics  -> bounded aggregate latency/rate/degradation
  PostgreSQL audit    -> durable actor/resource/business evidence
```

## Deterministic domain workflows (S1)

`app/workflows.py` owns customer-scoped order lookup, refund eligibility and
preparation, return preparation and the confirmed local submission boundary. Tools
adapt these typed operations; the orchestrator handles routing, response mapping
and the existing action lifecycle coordination. See [workflow decisions](WORKFLOWS.md)
for authorization snapshots, transaction ownership and explicitly deferred S2 work.

## Durable state vs distributed coordination

The architecture intentionally does **not** make Redis the system of record.

### PostgreSQL owns durable facts

- order/ticket state;
- customer, AI and assigned-agent ticket messages;
- pending action kind, payload and status;
- `created_at` and durable `expires_at`;
- action receipt keyed by the action/idempotency key;
- audit history.

Alembic revision `20260814_0002` adds the pending-action lifetime fields and indexes `expires_at`. Existing pre-v0.5 pending rows are backfilled to immediate expiry instead of being granted a new unbounded confirmation window. Revision `20260825_0003` adds durable `ticket_messages` for customer, AI and human-agent handoff conversations.

### Redis and database responsibilities after S2b

PostgreSQL owns semantic operation uniqueness, order/operation row locking,
version-checked transitions, customer confirmation, JWT-admin approval, receipts and
atomic business audit. Redis/local adapters still provide request rate limiting;
legacy action lease/TTL methods are not used for business submission.

## Durable operation resolution (S2b)

The existing pending_actions table is the canonical operation record. Requests for
the same tenant/customer/order/kind reuse an active or submitted operation. A partial
unique index participates in safety. Commands lock order then operation, revalidate
the immutable authorization snapshot, and commit local order state, receipt, required
audit and terminal operation state together.

Customer confirmation and high-risk approval are independent durable facts. Only a
JWT-authenticated support_admin can make the human decision. Drift is terminal
INVALIDATED; cancelled, expired, rejected and invalidated operations can be followed
by a new preparation with fresh authority. SUBMITTED means a local request
record, not payment settlement. No executing state or distributed exactly-once claim
is introduced. See [the lifecycle, constraints and API](OPERATIONS.md).

## Rate limiting

Redis uses an atomic Lua-backed **fixed-window** counter. Current API scopes include support messages and action confirmations. Counters are keyed by authenticated principal subject plus scope.

Exceeded limits return HTTP 429 and `Retry-After`. This is intentionally described as fixed-window; it is not a sliding-window or token-bucket implementation.

## Failure policy

Redis startup retains its configured check. S2b uses PostgreSQL locks/constraints and atomic transactions; Redis action locks no longer gate submission. Rate-limit Redis failure defaults to fail-open and is separately configurable. Dense retrieval failure may visibly fall back to BM25 when enabled while preserving the answerability threshold; strict mode propagates the failure.

Lock-release or TTL-cleanup failures **after** a completed mutation are audited as degraded; they do not roll back a business mutation whose durable receipt/state has already been written.

## Observability plane

The application creates OpenTelemetry spans around HTTP request handling and controlled application operations rather than treating audit events as performance traces. W3C `traceparent` is accepted at the HTTP boundary. The OTLP/HTTP exporter is optional and configured with an external full traces endpoint.

Representative operation spans include routing, hybrid/dense retrieval, embedding, Qdrant startup synchronization, refund/return policy, Redis reliability operations and business tools. The exact span set depends on intent.

Prometheus exposes process-local counters/histograms at `/metrics`. Metric labels are restricted to low-cardinality dimensions: route template, method/status class, component, operation, backend, outcome and typed degradation reason. Customer/order/action/ticket/trace IDs and raw message/model text are excluded from metrics labels.

This separation gives three different evidence layers:

```text
PostgreSQL audit -> durable business/security accountability
OpenTelemetry    -> per-request causality and component timing
Prometheus       -> aggregate rate/latency/degradation trends
```

The repository does not bundle Prometheus/Grafana/OTel Collector infrastructure and does not claim a production alert/SLO threshold. See `docs/OBSERVABILITY.md`.

## Identity and authorization

S2a requires a validated tenant in the trusted Principal and workflow context. Production defaults to JWT and fails before service initialization without issuer/audience/JWKS configuration; development headers and fixed MCP identity require explicit development mode. JWT verifies signature, issuer, audience, exp, iat, sub and tenant_id. Every operational table has tenant ownership, and security-critical reads/updates use tenant SQL predicates plus customer scope on customer paths. Support employees remain inside their authenticated tenant. MCP read tools require support:read; refund preparation additionally requires support:write and still has no final confirmation tool. See [tenant, identity and migration decisions](TENANCY.md).

## Knowledge lifecycle and retrieval

The current corpus is system/demo shared knowledge. S2a does not provide tenant-specific private knowledge isolation.

Versioned bundled knowledge records are deterministically chunked into stable `document_id:version:ordinal` identities with SHA-256 content hashes. Qdrant dense synchronization compares desired chunk hashes against remote payload metadata, embeds/upserts changed chunks only and deletes stale vectors. BM25 stays application-side; weighted RRF, score-aware reranking and the answerability gate remain common to local and Qdrant modes.

The local deterministic vector is a regression representation, not a semantic embedding model. Qdrant integration uses a real service but does not claim cluster HA or external-model semantic quality.

## Human workflow, ticket messages and audit

Tickets maintain priority, assignee, SLA deadline and validated state transitions. A handoff stores
the initiating customer request and AI handoff response as ticket messages. The assigned agent can
open the conversation and send a customer-visible reply; the reply moves an assigned ticket to
`pending_customer`. A customer reply moves it back to `assigned`. Closed and resolved tickets
reject new messages.

Ticket message ownership is derived from the server-side ticket record. Customer reads and writes
require the ticket's `customer_id`; agents cannot reply until assigned and cannot reply to a
ticket assigned to another agent (except a `support_admin`). This prevents a client from selecting
another customer's conversation ID. Internal ticket transition notes remain separate from
customer-visible messages.

Business tools, policy decisions, knowledge degradation, action preparation/expiry/cancellation/
execution, ticket transitions and agent message delivery emit durable trace-linked audit records.

## Release/CI contract

GitHub Actions starts PostgreSQL 17, standalone Redis 7.4 and Qdrant v1.18.2. It verifies Alembic upgrade/drift, unit/coverage gates, the 140-case safety set, 60-case retrieval set, the deterministic 60-request observability latency regression, real Redis reliability integration, real Qdrant lifecycle integration and Docker build.

Current adapter boundaries:

- FastAPI API; JWT/JWKS or explicit dev principal headers;
- SQLAlchemy + PostgreSQL/SQLite; Alembic schema evolution;
- Redis local/standalone coordination adapter;
- MCP Python SDK v2;
- OpenAI-compatible optional LLM and embedding providers;
- BM25 + in-memory/Qdrant dense retrieval;
- OpenTelemetry SDK + optional OTLP/HTTP trace exporter;
- Prometheus process-local registry exposed at `/metrics`;
- versioned bundled JSON knowledge source.

Not yet claimed: Redis Sentinel/Cluster HA, lock renewal, distributed exactly-once execution, external payment-provider idempotency verification, bundled Prometheus/Grafana/OTel Collector deployment, production alert/SLO thresholds, token/cost accounting, external knowledge publication workflows or production semantic/model quality.
