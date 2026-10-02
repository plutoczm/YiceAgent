# Open-source references and provenance

This repository is implemented as a new project. No upstream repository was renamed or copied wholesale.

Architecture and product-design references used during planning include:

- `ntg2208/production-ai-customer-support` (MIT): production customer-support agent patterns, guardrails, and evaluation ideas.
- `huabeitech/agent-desk` (Apache-2.0): helpdesk product concepts such as AI-to-human handoff, tickets, knowledge bases, and customer-support workflows.
- `modelcontextprotocol/python-sdk` (MIT): official MCP Python SDK and protocol integration patterns.

The implementation in this repository uses its own domain model, persistence layer, policy boundary, workflow, API contract, tests, and evaluation suite. If source code is later imported from an upstream project, the relevant license notice and file-level provenance must be preserved here.
