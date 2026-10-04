# Retirement progress

2026-10-02: User explicitly authorized removal. Clean Git baseline 9ecdb82.
Adjusted local provenance verification, added three adversarial tests and updated
current supersession notes. Lint/format/new tests passed. Full pre-deletion P6
and acceptance passed. Tested safe removal on actual Windows junction and owned
read-only disposable data; outside marker survived.

Executed Python-only deletion of three exact original repository directories,
then .runtime/private-resources. All four paths absent; deletion manifest stored
in ignored .runtime/repository-retirement/deletion.json. No original Docker or
Conda resource touched. Post-delete doctor/698 hashes passed. Complete regression
after actual deletion is running before final completion record.

Post-deletion complete: root 44, Harness 291, Support 219+1 skip, Analytics 284+1 skip, all real HTTP gates, 24 E2E, D5 50/50 and 12 AC passed. Updated user-authorized source-retirement clause in SPEC/current README; frozen manifest and historical audit retained. Four paths absent, original Conda executables present, public DB fingerprint unchanged, zero test schemas, HTTP stopped. Final deletion and proof records saved. No commit/push.
