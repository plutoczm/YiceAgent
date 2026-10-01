# Governed Business Analytics RC1 Baseline

## Release scope

RC1 freezes the DataCopilot D1–D5 workflow for local review as one milestone. It includes the application boundary, accepted business-data contract consumer, deterministic managed Text2SQL planning, governed Delivery v2 snapshot execution, trusted identity and tenant grants, authenticated API and Agent entrypoints, deterministic evaluation, tests, CI wiring, dependency declarations, and boundary documentation.

The D3 planning stage returns an authorized plan. Execution is a separate D4 workflow that validates a delivery and executes within a bounded in-memory DuckDB snapshot. Generic Text2SQL remains a separate workflow.

## D1–D5 summary

- **D1 — Workflow / Agent Boundary:** typed application models and ports prepare trusted scope without allowing Agent-controlled tenant or delivery selection.
- **D2 — Business Data Contract Consumer:** strict infrastructure parsing consumes the accepted Contract v1 artifact and maps its explicit datasets and fields into typed catalog models.
- **D3 — Deterministic Managed Text2SQL:** schema rendering and candidate generation produce a plan; SQLGlot-based structural and scope checks authorize it before it can reach D4.
- **D4 — Governed Business Analytics Execution:** the Delivery v2 consumer checks manifest, files, hashes, rows, scope, and budgets; exact directories come from trusted composition; effective fields are materialized into fresh in-memory DuckDB; translated SQL is reauthorized; execution and results are bounded.
- **D5 — Trusted Identity and Integration:** RS256 JWT/JWKS verification, explicit configured tenant grants, shared trusted context, authenticated API and Agent adapters, safe response models, and a deterministic two-tenant evaluation are included.

## Security invariants

- Tenant identity, grant, delivery directory, and execution context are resolved in trusted application composition. Request, Agent tool, prompt, memory, model output, and SQL cannot choose a tenant or directory.
- Identity and analytics are disabled by default. Missing runtime configuration fails closed; missing or invalid credentials are rejected, and authenticated subjects without an exact grant are denied.
- The managed query surface accepts bounded question/engine/dataset inputs. Public request and Agent tool schemas do not expose tenant IDs, schema context, delivery paths, or credentials.
- D4 validates the pinned contract and delivery before materialization, uses parameterized inserts into a fresh in-memory database, disables external access and extension loading, limits execution/results, and reauthorizes translated SQL.
- Audit events contain bounded operational metadata and opaque identity/grant fingerprints; they exclude bearer tokens, SQL text, row values, and local delivery paths.
- No automatic write execution or model-selected tool path is introduced. D3 returns `READY`; D4 is read-only and internal to the authenticated integration.

## Local validation baseline

The following local checks passed on the P0 working tree:

- `python -m pytest -q`: **492 passed**, 200 warnings.
- CI coverage command: **89.17%**, above the unchanged 85% gate.
- Deterministic enterprise analytics evaluation: **50/50 cases passed**. Routing, authorization allow/deny, SQL scope, and exact execution metrics are 100%; tenant isolation violations and unsafe-query accepts are both 0; negative API-auth cases pass 100%.
- `python -m pip check`, `python -m compileall -q backend tests scripts`, and Git whitespace checks passed.
- OpenAPI bearer security, closed request fields, Agent tool schema, public response serialization, and metadata-only audit behavior are covered by offline tests.
- Evaluation inputs and two-tenant Delivery fixtures use stable synthetic IDs and deterministic generation. No live LLM or production identity provider was used.

Test counts move with the suite; the command output is authoritative for a later checkout. The 200 local warnings are framework/Pydantic deprecations, ChromaDB `model_fields` deprecations, and OpenAPI JSON-schema warnings for `Path`-valued settings defaults. Pydantic excludes those non-serializable defaults from the schema. No PyJWT or DuckDB resource/security warnings appeared. These existing warning classes are recorded as maintenance debt, not as a security or production-readiness claim.

## Contract and dependency pins

- Accepted Contract identity: `supportops_business_data`, version `v1`.
- Accepted artifact: `contracts/external/business_data/v1/contract.json`.
- SHA-256 over the committed artifact bytes: `7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b`.
- Exact compatibility policy: `exact-artifact-sha256`, recorded in the adjacent `acceptance.json`.
- Direct runtime pins: `sqlglot==30.19.0`, `duckdb==1.5.5`, and `PyJWT[crypto]==2.15.0`.
- Local Conda, CI, and Docker install the shared `backend/requirements.txt` runtime set. Docker copies the accepted `contracts/` tree into the image.

The local milestone commit SHA is recorded by Git after the commit; resolve it with `git rev-parse HEAD` alongside this baseline. This note does not claim a deployed or externally published release.

## Validation still required

Production validation remains open. This baseline does not establish:

- Real production IdP, issuer, key rotation, or tenant-grant validation.
- A production Delivery mount, real business data, producer signatures, or producer authenticity.
- Database RLS, a durable audit ledger, or an OS-level DuckDB process sandbox.
- Live LLM quality, deployed SLO/latency, or operational observability baselines.
- A container build or startup in this environment. Docker CLI is present, but the Docker Desktop Linux engine is unavailable; container build, health, OpenAPI, route, and auth smoke checks are **pending environment**.
- GitHub Actions execution or production deployment.

After RC review, the next stage is **Production Validation P1 — Push / Pull Request / GitHub CI + Real IdP Deployment Validation**. No push is part of this P0 baseline.
