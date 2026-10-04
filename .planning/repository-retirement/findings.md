# Retirement findings

Normal verify_import previously required original filesystem/Git reads. Default
now verifies all 698 local files against frozen historical hashes. Explicit
--verify-originals retains strict original checks, with no silent fallback.

New tests cover missing originals succeeding in default mode, tampered local
source failing, and missing originals failing in explicit mode. Frozen manifest
is unchanged; source_git_unchanged is null when originals are not checked.

Pre-deletion full gates passed: root 44, Harness 291, Support 219+1 skip,
Analytics 284+1 skip, original HTTP gates, 24 E2E, 50 D5, 12 acceptance criteria.
Actual Windows junction deletion preserves its destination, rejects outside
paths, and removes owned read-only files. Four exact targets resolved under
intended parent directories; no nested link/junction traversal.

Deletion succeeded, archives removed last. Docker containers/volumes/images,
three Conda environments, current WorkflowOps PG/data and evidence are retained.
Post-deletion doctor and 698 local provenance hashes already passed. Full
post-deletion regression currently running; historical activation cancelled.
