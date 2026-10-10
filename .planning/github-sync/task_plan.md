# GitHub sync

User authorized sync of current YiceAgent to the existing same-name GitHub repository. Authenticated connector owner plutoczm; target https://github.com/plutoczm/YiceAgent, public, main.

1. Inspect refs/auth and publication contents including history: COMPLETE
2. Preserve current work in a local commit, configure repository-only remote: IN_PROGRESS
3. Push current main and existing version tags without force; verify remote: PENDING

Never push runtime, credentials, models, Codex internal refs, or synthetic test output as new model acceptance. Existing Git metadata physical move remains user-deferred; shared Git pointer is valid.
