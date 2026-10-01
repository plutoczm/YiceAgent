# WorkflowOps Agent Instructions

## Project Goal

WorkflowOps is an internship interview demonstration project.

The goal is not to build a production-scale autonomous multi-agent platform. The goal is to demonstrate sound AI application engineering through a small, coherent, reproducible business workflow.

Prefer the simplest architecture that clearly demonstrates the required engineering capability.

## Source of Truth

Use the following documents according to their roles:

- `docs/SPEC.md` defines product scope, architecture boundaries, technical rationale, and acceptance criteria.
- `docs/PLAN.md` defines the implementation phases and validation requirements.
- `docs/SOURCE_AUDIT.md` records the verified baseline state of the original AgentOpsHub, supportops-ai, and DataCopilot projects.

Read only the documents relevant to the current task. Do not repeatedly load every document when the task does not require them.

If implementation reality conflicts with the Spec, report the conflict before changing the intended architecture.

Minor implementation details may be reflected in the Plan, but do not silently change product scope.

## Architecture Principle

This project is workflow-first.

If a business process can be represented reliably with deterministic Python code, validation rules, state machines, or a DAG, prefer that over autonomous Agent reasoning.

Use LLMs only where semantic understanding or generative capability provides clear value, such as:

- grounded knowledge answering;
- natural-language understanding;
- generating a SQL candidate;
- optional natural-language explanation of structured results.

Do not delegate the following decisions to an LLM:

- authorization;
- tenant or customer isolation;
- refund eligibility;
- approval decisions;
- SQL authorization;
- destructive operations;
- durable business-state transitions.

LLM output must be treated as untrusted input and validated by deterministic application code.

## Service Boundaries

The three original projects have different responsibilities.

### AgentOpsHub

Target role: overall Harness.

Responsible for:

- workflow orchestration;
- service invocation;
- run state;
- timeout and failure handling;
- tracing;
- evaluation integration.

Do not turn it into an unrestricted autonomous supervisor Agent.

### supportops-ai

Target role: customer-support business domain.

Responsible for authoritative operational business state such as:

- orders;
- tickets;
- refund or return requests;
- confirmation;
- approval;
- human escalation.

Other services must not bypass its business rules to mutate SupportOps-owned state.

### DataCopilot

Target role: demo-data and governed analytics domain.

Responsible for:

- deterministic synthetic demo data where specified;
- business-data delivery consumption;
- validation;
- governed SQL analysis;
- optional natural-language-to-SQL candidate generation.

It must not independently maintain a second authoritative copy of SupportOps operational state.

## Environment Boundaries

Do not merge the three existing Python environments merely for convenience.

Use their existing Conda environments unless the Plan explicitly changes this:

- `agentopshub`
- `supportops-ai`
- `datacopilot`

Prefer commands such as:

```
conda run -n agentopshub ...
conda run -n supportops-ai ...
conda run -n datacopilot ...
```

This avoids accidental dependency contamination between services.

## Scope Control

Do not introduce technology solely to make the project appear more sophisticated.

The first interview-demo version does not require:

- autonomous multi-agent negotiation;
- A2A;
- ANP;
- GraphRAG;
- model fine-tuning;
- LoRA or QLoRA;
- Kubernetes;
- a complex long-term-memory platform;
- multimodal processing.

RAG, MCP, Docker, Tool Calling, and model-backed features should only be added where required by the Spec or the current Plan phase.

## Implementation Workflow

Work on one Plan phase at a time.

Before modifying code:

1. Read the current phase in `docs/PLAN.md`.
2. Read the relevant portions of `docs/SPEC.md`.
3. Inspect the affected existing implementation.
4. Confirm that the proposed change reuses existing functionality where appropriate.

During implementation:

1. Make the smallest coherent change that satisfies the phase.
2. Prefer deterministic implementations.
3. Preserve existing service boundaries.
4. Add or update tests with implementation changes.
5. Do not silently work on later Plan phases.

After implementation:

1. Run relevant lint or formatting checks.
2. Run relevant type checks when configured.
3. Run unit tests.
4. Run integration tests required by the phase.
5. Run the phase-specific acceptance checks from `docs/PLAN.md`.
6. Review the resulting diff.
7. Update Plan progress/evidence only after validation succeeds.

Do not mark a phase complete merely because code was written.

## Git Safety

Before substantial changes, inspect the working tree.

Preserve pre-existing uncommitted work.

Do not discard, overwrite, reset, or clean unknown user changes.

Do not push to a remote repository unless explicitly requested.

When a Plan phase has passed its validation gates, prepare a concise summary of:

- files changed;
- architecture decisions;
- tests executed;
- test results;
- remaining limitations.

A commit may then be created when explicitly requested or when the current task explicitly includes committing.

## Completion Rule

The project is complete when the acceptance criteria defined for P6 in `docs/PLAN.md` and `docs/SPEC.md` pass.

Do not automatically create additional architecture phases after P6.

After P6, prioritize demo reliability, documentation, and interview explainability over additional features.