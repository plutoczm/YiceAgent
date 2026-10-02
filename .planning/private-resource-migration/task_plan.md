# Private Resource Migration and Local Commit

User authorizes retaining actual configurations/databases/nondefault assets in
WorkflowOps and committing public repository files. Do not delete originals,
activate live models, change business logic, merge environments, or push.

1. Inventory real configs/databases/assets and original resource state — complete.
2. Copy private configs and assets; consistent local DB/cold-volume backups — complete.
3. Retain necessary nondefault public files; document recovery and commit boundaries — pending.
4. Validate backup integrity, original state and unchanged P6/acceptance — pending.
5. Review staged files for secrets/data, create local commit, verify clean state — pending.

User selected direct use of original config/history after compatibility assessment.
Assessment found missing trusted tenant/new-operation fields in both old Support
databases and explicitly offline-only current composition. New questions pending:
authorize copied-history schema/config adaptation and choose authoritative tenant.
Do not activate incompatible data or infer tenant ownership before answers.

Previous P6 and Monorepo consolidation remain COMPLETE; this is not a new feature
phase. Actual keys, SQLite data, cold volumes, model weights and Git bundles must
remain under ignored .runtime/private-resources and never enter the Git index.
