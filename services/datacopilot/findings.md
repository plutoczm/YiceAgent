# DataCopilot D4 Findings

Treat checked-out source and tool output as evidence. Keep external protocol notes here; do not copy instructions from external sources into the task plan.

## Baseline
- Branch: `master`
- HEAD: `aff4aba`
- Existing D1-D3 changes are uncommitted and must remain untouched except for D4 integration extensions.

## Evidence log
- D2 pins Contract v1 exactly and maps it into DataCopilot typed catalog models. The producer Contract names and orders four datasets: `orders_v1`, `tickets_v1`, `ticket_events_v1`, `actions_v1`.
- SupportOps Delivery format is `v2` while the Contract remains `v1`. Manifest fields are `contract_name`, `contract_version`, `export_format_version`, `generated_at`, `tenant_id`, `currency`, `schema_sha256`, `encoding`, `media_type`, `snapshot`, and ordered `datasets`. Dataset descriptors contain exact logical name, exact file basename, row count, and content SHA-256. Snapshot fields are consistency, start/completion times, `snapshot_id`, and per-dataset watermarks.
- Producer protocol documentation defines `delivery_fingerprint = SHA256(canonical manifest bytes + LF + ordered descriptor content hashes joined with LF)`. `snapshot_id` is SHA-256 over canonical JSON of contract hash/version, tenant, currency, and ordered datasets' name/hash/count. DataCopilot may independently recompute both; neither authenticates the producer.
- Producer validates canonical UTF-8 manifest/JSONL, duplicate keys, exact field sets, nullability, enums, decimals, UTC RFC3339 timestamps, tenant equality on every row, PK uniqueness, relationships, file names/types, hashes, row counts, snapshot metadata, and observed watermarks. Documented current upper bounds are 1 MiB for manifest and each JSONL record. D4 must set stricter total-input and per-dataset row budgets.
- Watermarks: `orders_v1` and `actions_v1` are `unavailable`; `tickets_v1` uses max `updated_at`; `ticket_events_v1` uses max `created_at`. `generated_at` is artifact creation time, not a business watermark.
- `currency` is trusted delivery-partition metadata, not a per-row field or proven source-money currency. Contract monetary values are decimal strings, not binary floats. Delivery consistency metadata describes exporter read mode; it does not make SHA-256 an authenticity signature.
- No SupportOps Python module was imported or edited; inspection used source/docs text only.
- Official DuckDB research: current PyPI stable is `1.5.5`, MIT, Python >=3.10, with CPython 3.11 wheels. Official docs define `enable_external_access=false`, autoload/autoinstall extension controls, `lock_configuration`, `TIMESTAMPTZ`, and Python connection APIs. Cancellation/Python interrupt and Decimal runtime behavior still require local execution proof.
- Official DuckDB Python docs expose `DuckDBPyConnection.interrupt()` and `fetchmany()`; the Python DB-API documents `:memory:` as non-persistent. The Python interrupt must still be proven cross-thread against the pinned wheel in this environment.
- DuckDB numeric docs state fixed-point DECIMAL is exact up to width 38, while division over DECIMAL uses floating-point. D4 therefore must not silently claim exact decimal semantics for division; the initial supported SQL subset will reject division until separately specified/tested.
- DuckDB timestamp docs describe `TIMESTAMPTZ` as timezone-aware with microsecond precision. Delivery v2 uses canonical UTC RFC3339 timestamps; the consumer can parse aware datetimes and bind them without dropping the offset.
- DuckDB external-access documentation says `enable_external_access=false` blocks file/network readers, `ATTACH` to files, and `COPY`; security settings can be locked. D4 will additionally disable extension autoload/autoinstall and lock settings before candidate SQL. Temp spill behavior under the proposed bound still requires local verification.
- Official references reviewed: https://pypi.org/project/duckdb/ ; https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview ; https://duckdb.org/docs/current/operations_manual/securing_duckdb/securing_extensions ; https://duckdb.org/docs/current/clients/python/reference/ ; https://duckdb.org/docs/current/sql/data_types/numeric ; https://duckdb.org/docs/current/sql/data_types/timestamp.html .
- Installed exactly `duckdb==1.5.5` into the existing `datacopilot` Python 3.11 environment with `--no-deps`, as authorized by D4. Runtime checks verified disabled external access/autoload/autoinstall/community extensions, configuration lock, `max_temp_directory_size = 0 bytes`, and that external `read_csv`, `ATTACH`, `COPY`, and extension INSTALL are rejected.
- Real pinned-package cancellation probe ran `SELECT SUM(HASH(i)) FROM RANGE(1000000000000)` in one worker, called `connection.interrupt()` from another, observed `InterruptException`, and confirmed the worker joined. This is actual engine evidence, not only a fake executor test.
- DuckDB TIMESTAMPTZ Python binding failed in this environment because `pytz` is absent. D4 avoids that dependency: it validates canonical UTC timestamps, normalizes to UTC naive `TIMESTAMP` at materialization, sets engine timezone to UTC, and adds UTC when serializing results. Decimal + naive UTC timestamp roundtrip worked locally.
- D4 consumer test suite currently passes `18 passed`; covers a synthetic four-dataset fixture, manifest and row tampering, tenant mismatch, field/type validation, fingerprints, budgets, path traversal, and symlink detection. D4 executor suite currently passes `23 passed`; covers real D1-D4 execution, dialect translation, Decimal, sandbox and real/fake interruption.
- Initial executor sandbox verification used `duckdb_settings()` and compared string-form settings to Python bools, causing false failures. Replaced the check with typed `current_setting()` results; the sandbox and both D4 test suites now pass.
- Expanded combined D4/D1-D3/D2 focused tests: `153 passed`. A source join test showed SQLGlot models boolean `AND` as a `Func`; the D4 function subset now skips AST operator nodes while still allowing only an explicit scalar/aggregate function set.
- Windows rejected unprivileged symlink creation during fixture testing. A deterministic path predicate test exercises the symlink rejection branch; it is not represented as an actual on-disk symlink integration test.
- Full regression passed: 368 tests, 190 warnings. CI coverage passed at 88.36% (85% minimum); delivery consumer 86%, combined DuckDB executor/materializer 77%, D4 workflow 72%, logging audit adapter 100%. Materialization is being separated to report its own coverage rather than hide it in the executor total.
- After the materializer split and added isolation/rollback tests, the full suite is `372 passed, 190 warnings`; unchanged CI coverage gate is `88.50%`. Separate D4 coverage is consumer 86%, materializer 89%, DuckDB executor 77%, D4 workflow 72%, audit adapter 100%.
- Final Git baseline remains branch `master`, HEAD `aff4aba`; no staged changes. Tracked `git diff --stat` shows 6 files changed, 108 insertions, 21 deletions; it omits untracked D1-D4 files. SupportOps remained outside the write scope; observed S3 artifact mtimes are 2026-09-22/23.
- D4 limitations are documented: no signed producer authenticity, production tenant identity/RLS/API/Agent/UI, live OLTP, durable audit ledger, or OS process sandbox. Windows did not allow real symlink creation in this session, so the rejection predicate is unit tested with a simulated symlink path.


## D5 baseline and proposal
- D5 started from branch `master`, HEAD `aff4abaf5262807a1b276e0df7401b3e793cc474`; D1-D4 changes are present and uncommitted, with no staged changes. Tracked baseline diff is six files (108 insertions, 21 deletions); untracked D1-D4 artifacts remain in the working tree.
- Current API has no trusted HTTP identity verifier or auth dependency. Agent `user_id` is client-provided on `AgentRequest` and is used for memory keys; it is not a tenant identity.
- Proposal: PyJWT JWKS verification with an explicit algorithm allowlist, exact issuer/audience, required subject/expiration, bounded clock skew/token size/JWKS timeout/cache; config-backed exact principal grants with one effective tenant; shared context factory; new authenticated analytics API and Agent entrypoint; offline multi-layer golden evaluation.
- Tenant grants, accepted Contract identity, and exact delivery directories are server configuration. Dataset names are checked against D2's pinned contract. Omitted identity wiring yields 503; missing/invalid bearer yields 401; authenticated principals without grants yield 403.
- The existing CI/Docker/Conda installation paths all read `backend/requirements.txt`; CI enforces 85% coverage. Evaluation must be deterministic and must not make real provider calls.


## D5 implementation evidence
- PyJWT 2.15.0 is the current stable package selected for this phase. Official PyPI metadata lists MIT and Python >=3.9; the crypto extra installs cryptography for RS256, and PyJWKClient exposes bounded JWK Set cache TTL, request timeout, unknown-kid refresh, and refresh cooldown.
- Added `PyJWT[crypto]==2.15.0` to the shared backend requirements path and installed it into the project Conda environment for local verification. `cryptography` and `cffi` were installed transitively; no OAuth server package was added.
- New principal/grant/context types are immutable and closed. The verifier requires RS256 and exact configured issuer/audience, required iss/sub/aud/exp, optional nbf/iat validation, 8 KiB token bound, bounded skew, JWKS TTL/timeout, and fail-closed network behavior.
- Config-backed exact `(issuer, subject)` grants are validated against D2 accepted Contract names/version/datasets. Exact tenant directories must match grant tenants, resolve to unique real directories, and are never derived from request input.
- Added authenticated `POST /api/v1/business-analytics/query`, authenticated Agent JSON and SSE routes, safe DTO/error mapping, server-generated request IDs, subject/grant fingerprints in metadata-only audit, and a server-only AgentExecutionContext. Generic Text2SQL stays separate.
- Added two versioned synthetic tenant Delivery v2 fixtures and a 50-case evaluation (routing 12, authorization 6, SQL scope 8, execution 18, API auth 6). Initial fixture/issuer mismatches were corrected; current report is routing 100%, allow/deny 100%, SQL scope 100%, exact execution 100%, isolation violations 0, unsafe accepted 0, API auth negatives 100%.
- Focused identity, API, Agent, and settings regressions currently pass: `59 passed`. Full-suite/coverage validation remains.
- Production limits documented: no production issuer/grants, one tenant per principal, RS256 only, no RLS, no signed producer authenticity, no durable audit ledger, and no deployed identity/tenant mapping.

- Final D5 full suite: `492 passed, 200 warnings`. The unchanged CI coverage command passed at `89.16%` against the 85% gate. Focus coverage: PyJWT verifier 94%, configured tenant resolver 100%, identity context factory 100%, API identity dependency 98%, managed API/Agent routes 85%, Agent adapter 100%, runtime composition 88%.
- Final golden runner CLI: 50/50 gate pass; routing 100%, auth allow/deny 100%, SQL scope 100%, exact execution 100%, tenant isolation violations 0, unsafe query accepted 0, API auth negatives 100%.
- `compileall` passed, changed Python line-length scan reported 0 lines above 100 characters, trailing whitespace scan reported 0, `git diff --check` passed, and `pip check` reported no broken requirements. Ruff/Black/mypy/pyright remain unconfigured and were not introduced.
- Final Git review: still `master` at `aff4abaf5262807a1b276e0df7401b3e793cc474`, no staged diff and no commits. Tracked-only diff stat reports 19 files, 394 insertions, 31 deletions; it omits untracked D1-D5 artifacts and synthetic fixtures. D1-D4 changes were preserved.

## Production Validation P0 audit
- Created local branch `feat/governed-business-analytics` from original HEAD `aff4abaf5262807a1b276e0df7401b3e793cc474`; no product files were lost or staged during branch creation.
- P0 freezes D1–D5 in one milestone. Explicit local add/commit is authorized; push/P1, reset/clean, and edits to SupportOps or AgentOpsHub remain prohibited.
- Full candidate source/test/fixture/docs/config scan found no private-key headers, JWTs, bearer credentials, credential assignments, or absolute local-path literals after replacing four synthetic `D:/private/...` test inputs with relative paths.
- Identity test keys are generated in-memory per test with `cryptography`; no fixed private-key fixture exists. `.env.example` disables identity/analytics and uses an invalid example issuer, empty grants/directories, and an empty API-key placeholder.
- Accepted Contract v1 identity is `supportops_business_data`; SHA-256 over exact artifact bytes is `7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b`, matching `acceptance.json`.
- Delivery fixture review covered 17 files. Email/phone pattern scan found no matches; tenant-a and tenant-b Delivery v2 fixtures regenerated in a temporary directory and matched every checked-in byte.
- Conda, CI, and Docker use `backend/requirements.txt`; direct pins are SQLGlot 30.19.0, DuckDB 1.5.5, and PyJWT[crypto] 2.15.0. Docker copies `contracts/` into the image.
- Full regression: `492 passed, 200 warnings`; CI coverage: `89.17%` (85% required); broad D1–D5 and Agent/Text2SQL/SQL Review focus: `327 passed`.
- Two deterministic evaluation runs returned identical 50-case metrics: routing/auth allow/auth deny/SQL scope/exact execution 100%, API negative auth 100%, tenant isolation violations 0, unsafe query accepted 0.
- `pip check` and `compileall` pass. OpenAPI bearer security, closed request fields, Agent tool schema, public serialization, and metadata-only audit have offline test coverage. Warnings are framework/ChromaDB deprecations and Path-default JSON-schema warnings; non-serializable defaults are excluded from schema.
- Docker CLI/Compose are present, but the Docker Desktop Linux engine pipe is absent. Container build and startup/HTTP/auth/import smoke were not run and remain pending environment.
- README test baseline and D1/D2 ROADMAP statuses were updated. `docs/BUSINESS_ANALYTICS_RC1.md` records scope, gates, pins, accepted Contract hash, and production limitations.

- Staged exactly 103 product files: 83 additions and 20 modifications (14,205 insertions, 33 deletions). Staged scan found no path/secret matches, forbidden artifacts, or task scratch; accepted Contract pin still matches.
- Updated interview guidance from the stale 212-pass/Docker-status statement and aligned the D5 next-stage wording with P1. Historical dated changelog retained.

- Local milestone commit: `062ee6136724fe4cc1f74baeb40e5fbe7e93bfce` (`feat: add governed business analytics pipeline`), parent `aff4abaf5262807a1b276e0df7401b3e793cc474`. `git show --stat` confirms 103 files, 14,205 insertions, 33 deletions.
- Post-commit 50-case deterministic evaluation passed with all recorded metrics; API/Agent integration smoke passed `24 tests`. `git diff HEAD` is empty; only the three task scratch files are untracked.
- P0 is complete with container validation pending environment. No push was made.


## Production Validation P1A
- The P0 RC SHA remains the base; no tracked drift was present at P1A start. `origin/master` initially matched the P0 parent, and the feature branch was not yet present remotely.
- GitHub CLI is not installed. Git Credential Manager write authorization was verified with a non-interactive push dry-run and normal push; no credential values were emitted or recorded.
- Docker daemon remains unavailable locally. Because the existing GitHub workflow had no image build coverage, a separate Ubuntu container-smoke job was added to CI. It builds the real backend Dockerfile/context from the compose configuration and exercises imports, exact Contract pin, startup, health, OpenAPI bearer/schema surface, and unauthenticated fail-closed behavior.
- The runtime image excludes `tests/`; deterministic 50-case evaluation remains in the existing clean Linux pytest job. The new job uses no production identity/grants/Delivery, model keys, Ollama, or registry push.
- After the workflow edit, local full tests, 85%-gate coverage, focused API/Agent/identity/workflow suite, and deterministic evaluation all passed. YAML and embedded smoke Python parsed/compiled; `pip check`, `compileall`, and `git diff --check` passed.
- Separate fix commit `ed5d15b8ddb1a8432519968ee7e2bbad098f638e` is pushed; remote SHA matches local. `origin/master` has no drift from the P0 base.
- PR #5 is open against master, has 103 changed files and no task scratch files, and is not merged. Final Actions run #133 on SHA `700fe0c6f92ee2c96925c7c9ed92ec8c32d26129` succeeded; PR merge state is clean.
- Initial run #132 remained queued on `ubuntu-20.04`; GitHub's official notice records that runner as retired on 2025-04-15. Moved pytest to `ubuntu-22.04`; no test or coverage gate was weakened.
- Linux CI result: `493 passed, 179 warnings`, coverage `89.15%` against 85%, 50-case deterministic evaluation PASS with all routing/auth/scope/execution metrics 100%, isolation violations 0, unsafe accepted 0. Real POSIX symlink test logged as PASSED.
- Linux container smoke: backend image build/import passed with SQLGlot 30.19.0, DuckDB 1.5.5, PyJWT 2.15.0 and cryptography 50.0.1; accepted Contract SHA matched; health, OpenAPI bearer/schema, and unauthenticated query 401 passed.
- PR body now records these results. PR #5 remains open/unmerged, 103 files, base `aff4abaf5262807a1b276e0df7401b3e793cc474`, head `700fe0c6f92ee2c96925c7c9ed92ec8c32d26129`, mergeable clean. Visible ruleset `DataCopilotAI` is disabled; classic branch-protection endpoint returned 401 so those settings could not be read.
- Local Docker daemon remains unavailable; container status is complete through GitHub Linux CI. The latest workflow run reported two nonblocking Node 20 deprecation annotations (checkout@v4/setup-python@v5 were forced to Node 24); all jobs passed.

- Final checklist added malformed-Bearer container smoke (non-200 assertion). Commit `facc6c31eeb902a80ea1d5521f12459f84f124bf` was pushed normally; final run #134 is green. The PR description now reflects run #134 and checks missing credentials (401) plus invalid Bearer (non-200).

## Production Validation P1B preflight
- Reconfirmed branch `feat/governed-business-analytics`, local/remote head `facc6c31eeb902a80ea1d5521f12459f84f124bf`, `origin/master` at the unchanged P0 parent, PR #5 open/unmerged, and no tracked drift.
- Source schema review confirms `TenantGrantConfiguration` fields: issuer, subject, tenant_id, allowed_datasets, contract_name, contract_version. `grant_id` is computed by the resolver; the identity verifier remains RS256-only, with exact issuer/audience and production HTTPS checks.
- Presence-only inspection covered the current process environment, ignored root `.env`, and tracked `docker/.env.production`. Real issuer/audience/JWKS, identity/analytics enablement, tenant grants, delivery map, and `DATACOPILOT_VALIDATION_BEARER_TOKEN` are absent in all three sources. The root `.env` exists and is gitignored; no values were displayed.
- No staging deployment URL/target environment variables are configured. The intended local staging Delivery path `[staging Delivery path withheld]` is absent. Compose service listing is only the ordinary app stack; P1A CI is not a deployment environment.
- Contract v1 bytes still match SHA-256 `7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b`. Existing offline fail-closed identity/API checks passed locally: `35 passed, 2 warnings`.
- No real IdP/JWKS request, real token, tenant grant, staging Delivery, production composition, or deployment request was attempted because the required inputs/target are absent. No code/config/GitHub PR changes were made; PR remains open.
- P1B is not complete: real IdP/token, real staging grant, staging Delivery, and deployment environment validations are blocked by missing external configuration. The attachment explicitly requires reporting `REAL IDP TOKEN = REQUIRED` rather than asking for it in chat.
- Read-only GitHub configuration metadata check found zero repository Actions secrets, zero repository variables, and zero configured Actions environments. No secret values were requested or returned.
## 2026-09-26 — P1B resumed preflight
- Rechecked branch/remote: local HEAD and `origin/feat/governed-business-analytics` are `facc6c31eeb902a80ea1d5521f12459f84f124bf`; `origin/master` remains `aff4abaf5262807a1b276e0df7401b3e793cc474`.
- Presence-only audit again found the real identity/JWKS/audience, grant JSON, delivery map, and transient validation bearer absent from process env, `.env`, and `docker/.env.production`. The latter files have non-empty environment/debug and app/service settings, but a comparison-only check found neither environment value is `staging`; only variable names were emitted. No secret/config values were read into output.
- The apparent host/base-URL settings are app bind or model/service endpoints; `docs/DEPLOYMENT.md` documents generic local/Docker deployment. No explicit staging target or target-specific delivery mount exists. The expected staging Delivery directory remains absent.
- Public GitHub PR page currently reports PR #5 Open. Remote refs match local/P0; anonymous REST API status refresh was rate-limited and `gh` is unavailable. No GitHub write was attempted.
- Temporary Python audit helpers were removed. No tracked or staged changes; the three permitted scratch files remain the only untracked files. No IdP request, bearer use, Delivery read, deployment, commit, or push occurred.
- Result remains `REAL IDP TOKEN = REQUIRED`; IdP, grant, Delivery, deployment target, and P1B remain blocked/incomplete. Do not start P1C.
## 2026-09-27 — Single-host bootstrap host gate
- The user approved a one-host staging topology and then explicitly excluded the only existing SSH alias. No alternate approved Linux staging alias or non-sensitive hostname is currently available.
- The local workstation is Windows 11; its Docker Desktop Linux engine endpoint is unavailable. It is not a Linux staging target.
- Before the exclusion arrived, read-only SSH diagnostics were run against the then-existing alias. No remote service, file, Docker container, or configuration was changed; no secrets were accessed. Disregard that host inventory and do not connect to that alias again.
- Bootstrap is stopped at host selection. The only required user input now is one approved Linux staging SSH host alias or non-sensitive staging hostname. Do not provide passwords, tokens, or secrets.
- No deployment, code change, commit, push, merge, or P1C work occurred. PR #5 remains open; the feature branch and P0 base references are unchanged.
## 2026-09-27 — P1B-B0 WSL2 read-only baseline
- Windows reports Windows 11 Pro, build 26200, x64; this shell is not Administrator. WSL is installed and reports version 2.7.10.0, kernel 6.18.33.2-2, and default WSL version 2.
- Before state restoration, WSL listed `docker-desktop` and `Ubuntu-24.04`, both WSL2. `Ubuntu-24.04` is Ubuntu 24.04.4 LTS; it has about 57 GiB under its user home and 523 installed packages. Treat it as a user distro and leave it untouched. It was stopped again after the read-only inspection.
- Existing Ubuntu has no systemd PID 1; its Docker CLI resolves to the Windows Docker Desktop path. Do not use it for native Docker or staging changes.
- `.wslconfig` is present with only these inspected fields: 8 processors, 12 GB memory, 4 GB swap; networkingMode is unset. WSL guest root is ext4 with about 896 GiB available; guest CPU count is 8. Clock difference was about 1 second. DNS resolved GitHub and Docker Registry, and HTTPS to GitHub returned 200; Docker Registry HTTPS still needs a clean response-code check in the isolated distro.
- `wsl --list --online` confirms `Ubuntu-24.04` is available. No WSL feature enablement, distro installation, optional feature change, reboot, Docker installation, product code change, PR mutation, or secret creation occurred.
- Decision: use a separate distro named `local-wsl-staging` because the existing Ubuntu profile is substantial. Continue only with a supported named-distro workflow that does not require unapproved Windows Administrator elevation or change global WSL settings.
## 2026-09-27 — WSL staging distro registered
- Used the installed WSL CLI's named-distro support with the verified online distro name `Ubuntu-24.04` and registered `local-wsl-staging` using `--web-download --no-launch`. The command completed without Administrator elevation or reboot.
- `wsl --list --verbose` confirms `local-wsl-staging` is Stopped and VERSION=2. The existing Ubuntu-24.04 developer distro remains separate and stopped.
- Stopped before first interactive account setup. No Docker Engine, distro systemd configuration, secrets, Keycloak, PostgreSQL, DataCopilot, or Delivery has been installed/created.
- User action: launch `wsl.exe -d local-wsl-staging` in a local terminal and finish Ubuntu's first-run normal-user setup. Do not transmit the password; report only when the setup is complete.
