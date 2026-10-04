# DataCopilot Production Validation P1B-B0 — WSL2 Native Linux Staging

## Objective
Establish an isolated local WSL2 Ubuntu staging distro named `local-wsl-staging`, native Docker Engine, and a durable Linux staging filesystem for later P1B. Do not deploy Keycloak/DataCopilot, configure identity/tenant/Delivery, change PR #5, or start P1C in B0.

## Current phase
A clean distro named `local-wsl-staging` was installed from the currently listed Ubuntu-24.04 source with WSL `--web-download --no-launch`, without Administrator elevation or reboot. WSL confirms it is registered as version 2 and Stopped. The next first-launch Ubuntu user setup is interactive; pause for the user to complete it locally without sharing the password. The previous user Ubuntu distro remains untouched and stopped. No Docker, services, secrets, or product code have been added.

## Work plan
1. [complete] Read the B0 instructions and retain the explicit local-wsl-staging scope; no SSH target is needed.
2. [complete] Read-only Windows/WSL/repo baseline and current Ubuntu suitability check; existing user distro remains untouched and stopped.
3. [complete] Confirm the supported named-distro install command and actual online name.
4. [complete] Install/register the clean `local-wsl-staging` Ubuntu-24.04 distro as WSL2 without Administrator changes or launch.
5. [waiting for user] Launch `wsl.exe -d local-wsl-staging` locally and complete Ubuntu's interactive normal-user setup; never send the password in chat.
6. [not started] Enable systemd only in the staging distro and verify PID 1.
7. [not started] Install Docker Engine from Docker's signed official Ubuntu apt repository; validate non-root Docker and Compose.
8. [not started] Run hello-world and temporary Compose smoke; verify WSL networking, certificates, DNS, clock, TCP exposure, and restart persistence.
9. [not started] Create the ext4 staging root and restrictive empty secrets directory; report resource limits.
10. [not started] Confirm Windows-to-WSL Docker invocation, repo/PR unchanged, and B0 completion gate.
11. [not started] Stop at B0 completion; do not begin B1.

## Established validation evidence
- Full pytest: 492 passed, 200 warnings.
- CI coverage command: 89.17% against the unchanged 85% gate.
- Deterministic evaluation: 50 cases pass; routing/auth/SQL scope/exact execution 100%; tenant isolation violations 0; unsafe query accepted 0.
- `pip check`, `compileall`, and diff/whitespace checks passed.
- Final `git fetch origin` confirmed `origin/master` remains the frozen P0 parent; no master drift or merge conflict.
- Docker CLI/Compose are installed, but Docker Desktop Linux engine pipe is unavailable locally. Remote Ubuntu CI built and smoke-tested the backend image successfully, so P1A container validation is complete.
- Current PR #5 is open and not merged; P1B must not merge it or start P1C.
- `gh` is not in PATH. Git Credential Manager is configured; no credential/token values were inspected.
- Remote Actions run 134 on final SHA: pytest, deterministic evaluation, coverage, and container-smoke all completed successfully. Linux test output confirms the real symlink case passed; container smoke confirms imports/pins/Contract SHA, health, OpenAPI, anonymous 401, and invalid Bearer non-200.
- Linux results: 493 passed, 179 warnings, coverage 89.15% (85% required); deterministic evaluation 50/50, every reported metric 100%, tenant isolation violations 0, unsafe accepts 0.
- Current local recheck after the POSIX-only test addition: 492 passed, 1 skipped on Windows, 200 warnings; coverage 89.17%. The test is confirmed PASSED on the Linux Actions run.
- Runtime grant schema is exactly issuer, subject, tenant_id, allowed_datasets, contract_name, and contract_version; grant_id is computed at runtime, not configured. Trusted identity is RS256-only and production issuer/JWKS must be HTTPS.
- Presence-only audit: expected IdP/analytics/token fields absent in process env, ignored root `.env`, and tracked `docker/.env.production`; root `.env` exists but remains ignored. No values were printed. No configured staging URL/target or mapped staging delivery directory was found.
- `docker compose config --services` safely listed chromadb, redis, backend, and frontend; this local app stack does not establish a staging deployment target. Contract bytes still match accepted SHA `7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b`.
- Offline fail-closed identity/dependency checks: `35 passed, 2 warnings`. These do not substitute for real IdP, grant, Delivery, or deployment validation.

## Guardrails
- Keep PR #5 open. Do not merge, modify master, force-push, amend, squash, rebase, reset, clean, delete task scratch, or start P1C.
- Use only an explicitly configured staging/test identity and synthetic or explicitly approved non-production Delivery. Do not use real production tenant/customer data.
- Do not output or commit bearer/refresh tokens, client secrets, private keys, raw subjects, raw claims, real grants, or Delivery paths. Never request a token pasted into chat; use the secure local environment/secret channel specified in the P1B instructions.
- Keep RS256 only; do not add algorithms, auth protocols, D6 capabilities, live LLM calls, public tunnels, DNS, firewall changes, or a new cloud target.
- Do not modify SupportOps or AgentOpsHub. Do not commit the task scratch files or P1B secrets/runtime Delivery.
- No real bearer token is present; do not generate a fake or ask for a pasted value. Report `REAL IDP TOKEN = REQUIRED` and list only non-secret variable names until the user configures the secure staging environment.

## Errors encountered
| Error | Diagnosis | Resolution |
|---|---|---|
| `python` alias returned exit 1 with no output | WindowsApps execution alias has no usable interpreter | Use `D:\Anaconda3\envs\datacopilot\python.exe` for project checks and Python file handling |
| Early inline audit command failed to parse | PowerShell stripped nested quote characters | Moved the audit to a temporary Python script and reran successfully |
| Scratch-note append command was cut off by PowerShell backtick parsing | Inline Markdown backticks were interpreted by the shell | Wrote scratch notes through a temporary Python script and removed that helper afterward |
| Inline YAML workflow extraction was parsed by PowerShell as redirection | Heredoc markers conflicted with shell parsing | Validated YAML and embedded Python with a temporary Python helper, then removed it |
| P1B presence-audit helper could not import repository modules from its scripts directory | Python sets the script directory at `sys.path[0]` | Added the repository root to `sys.path` in the temporary helper; reran safely and removed it |
