# P4 findings

- Producer already exports Delivery v2 using PostgreSQL REPEATABLE READ READ ONLY, sibling rename and explicit schema/field rules.
- Consumer independently verifies canonical bytes, hashes, contract, tenant, references, snapshot and budgets. DuckDB executor reauthorizes source/translated AST and bounds materialization/results.
- ManagedSQLGeneratorPort supports a deterministic candidate adapter without changing existing live generation or JWT/JWKS defaults.
- Demo services currently include Harness/SupportOps only. P4 adds DataCopilot via its own environment and explicit demo factory.
- Main demo DB has an existing ORD-1001 refund request from P3; preserve it. Real acceptance tests use the existing owned disposable schema mechanism.

- Real report oracle: tenant-a 12 orders; refund distribution pending_approval=1/submitted=1; pending approvals 1; open tickets 2; currently overdue 0. Stale snapshot has no actions; explicit refresh changes snapshot identity and rows. Old directory remains queryable through the governed pipeline.
- Windows Python 3.11 lacks Path.is_junction; reject Windows reparse-point attributes. Hive timestamp CAST parses to TRY_CAST outside the executor allowlist, so the template compares the typed timestamp to a fixed server-generated UTC literal without altering the policy.
- Four prescribed P4 gates and P3 regressions passed. Two old native-symlink skips are limitations, not passes; new Windows junction test and real PG isolation/read-only check pass.
