# S2a: trusted tenant and production identity boundary

S2b update: [OPERATIONS.md](OPERATIONS.md) supersedes the earlier operation,
approval, transaction and replay limitations described in this phase record.
Tenant/identity and core eligibility boundaries remain; historical results below
are retained as phase evidence.

SupportOps owns operational data and domain semantics. A tenant identifies one
enterprise. S2a adds identity and SQL isolation; it does not implement S2b.

## Trusted identity and defaults

Tenant IDs are validated opaque strings: 1-64 ASCII letters/digits/underscore/hyphen,
starting with a letter or digit. `TenantId` follows SupportOps string ID conventions,
not another project's UUID choice. Principal and workflow customer context are frozen.

`Principal(subject, tenant_id, customer_id?, roles, scopes)` is populated at the
authenticated entry boundary. JWT requires the exact `tenant_id` claim; there is
no configurable alias or default-tenant fallback. Signature, allowed asymmetric
algorithm, issuer, audience, exp, iat and sub checks remain. Invalid tenant, subject,
customer mapping, role representation or scope representation is rejected.
A customer role requires a customer ID. Support agents/admins may have no customer.

| Configuration | Behavior |
| --- | --- |
| No auth/environment configuration | `APP_ENV=production`, `AUTH_MODE=jwt`, `MCP_AUTH_MODE=jwt`; missing issuer/audience/JWKS fails before database initialization |
| Explicit development + dev / disabled MCP | Development headers establish subject/customer/roles; `DEV_TENANT_ID` is the server-configured tenant |
| Development + JWT | Verified JWT is authoritative |
| Production + JWT | Both HTTP and MCP require JWT configuration; demo seeding is forbidden |
| Production + dev HTTP or disabled MCP | Startup fails |
| JWT plus development identity headers | JWT wins; development headers have no authority |
| Dev mode plus a Bearer header | Rejected as conflicting identity sources |

Production needs `AUTH_ISSUER`, `AUTH_AUDIENCE`, `AUTH_JWKS_URL`, both auth modes
set to `jwt`, and `SEED_DEMO_DATA=false`. Signing algorithms are restricted to
RS256/384/512 and ES256/384/512. TLS, IdP operation, key rotation/revocation and
authoritative enterprise membership provisioning remain deployment responsibilities.

There is no customer or employee directory in this repository. Within a verified
tenant, the IdP's subject/customer/role mappings are trusted. An assignee string is
interpreted within that tenant, not as a globally authorized employee. Existing broad
intra-tenant list/assign/transition permissions remain; assigned-agent/admin reply
checks are preserved. This is not a full organization RBAC/IAM implementation.

HTTP request DTOs reject extra fields, including tenant/customer/roles/actor overrides.
Query parameters, X-Tenant-Id, model output and message text cannot choose tenant.
Existing resource response DTOs do not expose the new tenant column. Foreign tenant
resources are treated as missing; foreign and missing order/action/ticket IDs have
the same outward error contract. Cross-customer action denial now also uses the
scoped not-found result instead of loading a row and returning mismatch information.

Factories import without opening databases. Uvicorn's existing `app.main:app`
attribute and the MCP module entry point resolve the app lazily and validate security
before constructing services. The Docker entry command validates security before
running Alembic as well.

## Local demo and Compose

`docker-compose.yml` explicitly selects development + dev HTTP + disabled MCP,
and exposes the API only at `127.0.0.1:8002`. These are local demo choices, not
production authentication. Production environment overrides that leave dev auth
or demo seeding enabled are rejected.

`.env.example` contains explicit development settings; application code does not
automatically load a .env file. Compose loads its environment file. Direct local
Python/Uvicorn use must explicitly export development settings, for example:

```powershell
$env:APP_ENV = "development"
$env:AUTH_MODE = "dev"
$env:MCP_AUTH_MODE = "disabled"
$env:DEV_TENANT_ID = "TENANT-DEMO"
$env:SEED_DEMO_DATA = "true"
uvicorn app.main:app --host 127.0.0.1 --port 8002
```

Do not enable these settings on an exposed production deployment. The main synthetic
demo uses TENANT-DEMO; tests create an additional isolated enterprise. Demo resource
IDs remain global, so seeding the same fixed demo IDs separately for a second tenant
in the same database is not supported.

## Persistence and SQL scope

All seven operational tables carry direct, NOT NULL `tenant_id` with no ongoing
server or ORM default:

| Table | Ownership and access |
| --- | --- |
| orders | Every lookup/submission uses tenant + customer + order ID |
| tickets | Tenant-scoped lists/assignments/transitions; customer reads/recovery additionally scope customer |
| ticket_events | Tenant inherited from the scoped ticket when writing; direct provenance for history |
| ticket_messages | Tenant/customer inherited from scoped parent; customer reads/writes narrow both, agent reads/writes narrow tenant |
| pending_actions | Preparation binds tenant; lookup/confirm/cancel/expire/replay narrow tenant and, on customer paths, customer |
| action_receipts | Tenant-scoped lookup joins the same-tenant action and customer; checks action kind, order and refund quote before replay |
| audit_events | Each write carries trusted tenant; trace retrieval filters tenant + trace, never trace alone |

No tenant-owned table relies only on an unscoped parent or global actor/trace.
Existing resource primary keys remain **globally unique**, including order/action
IDs and receipt keys. Customer and agent strings may repeat in different tenants.
A colliding foreign receipt key cannot return its result; a conflicting insert
fails and rolls back the existing local transaction. This is not business deduplication.

Security-critical SELECT and UPDATE statements include tenant predicates in SQL.
Customer-owned queries additionally include customer where appropriate; defensive
Python comparisons are not the sole boundary. Store APIs require tenant with no
implicit default. Tests inspect SQLAlchemy's effective SELECT/UPDATE predicates as
well as behavior with two tenants and repeated customer strings.

New indexes cover actual customer order lookup, tenant/status ticket listing,
tenant/customer/conversation recovery, tenant/ticket message history,
tenant/status/action expiry and tenant/trace audit lookup. Existing global indexes
and primary keys remain. No speculative receipt index is added merely to duplicate
its globally unique key. S2a adds no RLS or parent foreign-key redesign; trusted
store writes derive child ownership from scoped parents.

## Migration and provenance

New Alembic revision: `20260915_0004`, following `20260825_0003`.
Historical migration files are unchanged.

An empty database upgrades normally. A nonempty legacy database has no tenant
provenance, so the migration refuses to proceed unless explicitly opted into the
synthetic/demo assignment:

```text
python -m alembic -x legacy_demo_backfill=true upgrade head
```

Only for a verified legacy/demo database, this assigns **every existing row in all
seven tables to TENANT-DEMO**, then enforces NOT NULL without a persistent default.
It is a compatibility assignment, not recovered historical enterprise ownership.

For the local Compose demo, the equivalent explicit opt-in is
`LEGACY_DEMO_BACKFILL=true` for the migration run; its default is false. Remove the
opt-in after migration. Real data needs an external authoritative mapping and a
reviewed migration, not the demo flag. Offline SQL generation is rejected because
this migration must inspect legacy state. Downgrade removes ownership columns and
must not be used casually on real multi-tenant data.

The real PostgreSQL test uses an explicitly isolated database named
`supportops_s2a` and a uniquely named temporary schema. It verifies old-head upgrade,
refusal without opt-in, backfill of all seven legacy row types, NOT NULL/no-default,
current head, Alembic drift, downgrade/re-upgrade and row preservation, then drops
only that test schema. Local execution used PostgreSQL 17.11 in a dedicated
temporary Docker container. SQLite create_schema tests supplement this, not replace it.

## MCP permission boundary

MCP keeps the same four tools. JWT verification and Principal mapping use the same
tenant validation as HTTP. No authoritative tenant/customer fields exist in tool schemas.

| Tool | Required permission |
| --- | --- |
| search_support_knowledge | support:read |
| get_order | support:read |
| get_ticket | support:read |
| request_refund | support:write, in addition to transport's baseline support:read |

Read/write scope names follow `MCP_REQUIRED_SCOPE` / `MCP_WRITE_SCOPE`; they must be
distinct. Read-only tokens cannot prepare refunds. Write-capable tokens still pass
tenant/customer ownership, eligibility, high-value policy and confirmation checks.
No final confirmation or raw execution tool is exposed. Disabled auth with a fixed
demo principal is allowed only in explicit development mode.

## Privacy and shared knowledge

Knowledge remains **system/demo shared knowledge**. Neither the static corpus nor
Qdrant/BM25/dense/RRF/reranking/answerability is tenant-specific. Do not ingest private
tenant material into this shared corpus and claim S2a isolates retrieval.

Tenant attribution is stored on audit rows; no tenant, customer or token labels were
added to metrics. JWTs, Authorization headers and raw message bodies are not newly
logged. The exact S0 initial-handoff bug is narrowly fixed: the customer message
copied from `/v1/support/messages` into ticket history is sanitized with existing
guardrails. A regression checks phone/email removal from persisted history.

This is **not comprehensive messaging privacy**. Subsequent customer/agent message
bodies, existing transition notes and historical data are not retroactively
redacted; their retention/redaction policy remains a later privacy task.

## S1 preserved and S2b deferred

Refund/return submission still records local `requested` state, not payment settlement
or a completed physical return. S1 authoritative revalidation and exact quote matching
are preserved: amount increases/decreases and lost eligibility cannot silently expand
confirmation authority. Existing transaction splits, cancellation, expiry and terminal
replay remain.

No RLS, business-operation identity, cross-request refund idempotency, business unique
constraint, row lock/CAS, durable high-risk approval, outbox/reconciliation, data exporter,
DataCopilot contract, AgentOpsHub integration or autonomous agent was added.

**Recommended S2b:** define business-operation identity within tenant, cross-request
deduplication and receipt binding, explicit atomic transaction ownership, database
uniqueness with locking/CAS, crash/replay semantics and durable high-value approval
snapshots bound to actor/amount/policy. Keep financial settlement outside the claim
until a verified provider integration and reconciliation contract exist.

## Executed S2a validation

Windows / Conda supportops-ai / Python 3.11.15, interpreter
`D:\Anaconda3\envs\supportops-ai\python.exe`. No dependency installation or .venv.

| Command (with that interpreter) | Result |
| --- | --- |
| `-m pytest -m "not integration" --cov=app --cov-report=term-missing --cov-fail-under=75` | 164 passed, 1 skipped, 3 deselected; 86.53% coverage |
| `-m pytest -m integration tests/integration/test_tenant_migration.py -s` | 2 passed on PostgreSQL 17.11; 11 existing Alembic path_separator deprecation warnings |
| `-m evals.run_evals` | 140 cases; metrics 1.0, unsafe mutations 0 |
| `-m evals.run_retrieval_evals` | 60 cases; metrics 1.0 |
| `-m evals.run_e2e_regression` | 61 scenarios; no failures |
| `-m evals.run_e2e_regression_v2` | 60 scenarios; gates pass, known scenario miss retained |
| `-m evals.run_slo_smoke` | 60 requests; zero errors/degradation, P95 45.03 ms under 750 ms |
| `-m ruff check .` | Pass |

The 56 new local boundary cases and two PostgreSQL migration tests supplement the
108 prior regression cases. JWT probes use generated RSA signatures with local
verification keys, not a real IdP. MCP probes exercise the SDK client/server with
verified token context and per-tool permission checks; no real remote MCP service
is called. SQL predicate checks inspect executed SELECT/UPDATE expressions rather
than exact SQL formatting.

V2 retains the single known `v2-knowledge-shipping-02` scenario mismatch:
tool selection/task completion 0.9833 and citation consistency 0.95. Safety and
cross-customer isolation remain 1.0. Frozen scenarios and gates were not changed.
CI is wired to repeat the isolated PostgreSQL contract; this local work does not
claim a new remote CI run or production deployment.
