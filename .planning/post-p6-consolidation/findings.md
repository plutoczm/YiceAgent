# Consolidation findings

- WorkflowOps already has its own root Git repository, three service snapshots,
  fixed subprocess runtime, and all P6 integration additions. Source consolidation
  must preserve the tested WorkflowOps implementation rather than overwrite it.
- Original Git heads: Harness 5daedb6; Support 90ba9a7; Analytics facc6c3.
  WorkflowOps HEAD 1fc0d39, with 13 pre-existing P6 changed/untracked files.
- Support original effective untracked Contract/export/validation files and tests
  already match their WorkflowOps copies byte for byte. Its five untracked
  egg-info files are generated metadata and excluded. Analytics untracked files
  are historical planning notes, already retained as provenance.
- Three environment/import probes passed inside WorkflowOps. Main runtime ports
  are 8010/8011/8012 and owned PostgreSQL 55432; no active HTTP demo state.
- Passed P6 baseline: .runtime/p6-f9bce7304606410198209f8ad86710c3;
  root 41, Harness 291, Support 219+1 skip, Analytics 284+1 skip;
  HTTP support/delivery/reports/ui 1/6/1/1, E01–E24 24, D5 50/50,
  AC-01–AC-12 pass. Source files 698 and original Git snapshots unchanged.
- Source .env.example templates were not imported by the original P1 security
  allowlist. Review placeholder safety and relevance before preserving templates.
- An ignored service-local Analytics data/logs/datacopilot.log exists after
  runtime imports. It is not tracked/copied source; inspect logger path options
  to keep future generated logs in root .runtime without changing business code.
- Original source READMEs contain install/model-download/independent Compose
  instructions. They must be clearly historical, not the Monorepo launch guide.

User selected physical services/support and services/analytics. 802 pre-existing
effective WorkflowOps files retained: initial manifest 274 unchanged in place,
509 byte-identical relocations, 19 reviewed path/config/document adjustments.
696 effective source files plus the two root contract copies still verify with
the historical 698-file source manifest. Three empty/placeholder upstream env
templates added as reference-only files; original untracked egg-info excluded.

Root cwd + an explicit service Ruff configuration changes relative src/per-file
resolution. Initial supplemental lint reports were invocation-scope differences,
not new domain-code defects. Harness affected tests pass from their service cwd;
Support affected snapshot test passes from its service cwd, and the untouched P6
state script passes its original root explicit-config gate. Analytics existing
broad HTTP-error sanitizer is unchanged; baseline rule set passes. Do not refactor
its exception behavior or add unrelated annotations/docstrings during migration.
