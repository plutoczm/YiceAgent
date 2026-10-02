# Private Resource Migration Progress

2026-10-02: Captured current Git counts, prior consolidation and source metadata.
Read-only resource inventory stored in .runtime/retained-assets/inventory.json;
no configuration values printed. Enumerated four real configs, local SQLite,
stopped original named volumes/bind mounts and existing model weights. Unrelated
Docker projects were inspected only for association and will not be touched.

Clarification sent for archive-only versus activation/default-data change.
Private preservation work can proceed independently; activation remains paused.

User selected original configuration/history use. Read-only assessment: source
Support SQLite has 3 orders, no tenant columns, no operation_approvals/new fields;
original PostgreSQL clone has the same old schema. Harness PostgreSQL public schema
empty; Analytics SQLite is Chroma knowledge/vector storage, not the governed
analytical business store. Source config enables external LLM/retrieval and automatic
schema/demo seed; current compositions are explicitly offline-only. Reported the
conflict and asked for explicit compatibility-adaptation/tenant ownership decisions.

Preservation complete: private directory retained-3e93acaeb2c54e28a580afb1ebdc3405;
90 original files (~7.1 GB including model/training outputs), 4 actual configs,
2 SQLite backup API snapshots with integrity/count equality, 6 read-only cold named
volume TARs with checksums/PG_VERSION, 3 verified Git bundles. All remain ignored.
Two cold PostgreSQL archives restored/start-inspected-stop in network-none clones,
no original database/container start or mutation; schemas recorded privately.
No actual values printed, original directories intact, no commit yet.

Independent current P6 baseline verification after private retention passed:
root 41, Harness 291, Support 219+1 skip, Analytics 284+1 skip; original real HTTP
gates and E01–E24 24/24, D5 50/50, AC-01–AC-12 pass. Historical-data activation
is still pending the explicit compatibility/tenant decisions, not claimed complete.

Public candidate secret gate checks all current tracked/nonignored paths against
actual configured long secret values; no matches/private data artifacts found.
Added ignore defense for key formats, DuckDB/WAL, weights and bundles; retained
safe original editor-manager metadata as a non-active reference. Commit author
is not configured in current Git settings; reuse current repository HEAD author
for this command via process-local -c settings only, no user/global config change.

User already authorized local commit: preparing one reviewed snapshot containing
completed P6/Monorepo and resource-preservation documentation. Private backups and
pending activation decisions remain outside this commit's completion claim.
