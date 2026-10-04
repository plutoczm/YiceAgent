# WorkflowOps v2 instructions

The user explicitly authorized SPEC_V2 and its full implementation. Read docs/SPEC_V2.md and docs/PLAN_V2.md from the repository root. They govern v2; v1 SPEC/PLAN and P1-P6 remain historical and unchanged. Work one v2 phase at a time and record actual gates before advancing.

Use Python for file automation. Use the unique workflowops namespace. Never import app packages from the old services or install into their environments. Preserve their sources and v1 data. Port/reuse security semantics with provenance and tests.

No invented model, retrieval, browser or PostgreSQL results. Model access, installation, model download, migration and paid calls follow the user's permission gate. Never print credentials or put them into model context, logs or Git. No commit/push without explicit instruction.
