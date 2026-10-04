# WorkflowOps Agent Instructions

## Scope and source of truth

WorkflowOps is a local learning and interview demonstration, with one current Python application. Read docs/SPEC.md and docs/PLAN.md for the active requirements and phase. Work one phase at a time; do not claim later phases complete or start them silently.

The user requires version management through Git commits, branches and tags, rather than parallel v1/v2/legacy source folders. The archived v1 implementation is reachable through v1.0.0; current code is under src/workflowops. Preserve history, licenses and provenance.

Use Python scripts for file automation, particularly Chinese and complex text. Inspect the working tree first and preserve unknown modifications. Do not reset, clean, overwrite unknown work, push or publish without authorization. Local commits/tags require a task that explicitly requests them; the current directory-normalization task includes Git version management.

## Architecture

Prefer a small coherent deterministic implementation. LLM output is untrusted. Authorization, tenant/customer isolation, eligibility, approvals, idempotency and durable state transitions belong to deterministic business code.

Use the unique workflowops namespace. Do not import historical app packages, combine old Conda environments or bypass business service rules. src/workflowops/business owns authoritative local business state. Keep confirmed domain results separate from provisional model text.

Main and inference environments remain isolated; Pi handles authentication/protocol only. Existing ignored .runtime/v2 paths and workflowops-v2 Docker resources are compatibility identifiers, not versioned source layouts. Never reset v1 or current business data while changing code layout.

Model access, dependency installation, downloads, migrations and paid calls obey the user's established approval scope. Never read another tool's login, print credentials or include them in model context/logs/Git. No invented model, retrieval, HTTP, database or browser results.

## Workflow

Before changes: read the relevant phase/spec and affected code, inspect Git, identify reusable behavior and any important tradeoff. Ask for material scope/irreversible/preference decisions; ordinary authorized reversible work can proceed.

After changes: run configured Ruff, configured type checks, meaningful unit/integration tests and phase acceptance checks, review the diff, then record actual evidence. Keep failures and skips separate from passes.

Entry points:
- scripts/manage.py: doctor, up/down, check, baseline, infra-plan and explicit provisioning/replay.
- scripts/models/: approved download helpers and local accelerator checks.
- scripts/maintenance/verify_layout.py: immutable historical Git source preservation; --strict also checks frozen layout outputs.
- start.cmd/stop.cmd: current local workbench, port 8020.

Tests live under tests/unit and tests/integration. Real integration uses the owned database and unique disposable schema; never point cleanup at public or a user-supplied target. Use the main environment for application tests and the inference environment for model inference. Do not install into agentopshub/supportops-ai/datacopilot.

The current whole-v2 completion criteria remain those in SPEC and PLAN. Completing the business or layout stage does not complete LLM/RAG/context acceptance. Do not extend the project into unsolicited multi-agent platforms, production authentication, public deployment or additional stages.
