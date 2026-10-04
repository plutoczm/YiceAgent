# P5 findings

- Existing seven fixed workflows cover all P5 business actions; retain server ACL, no new domain write endpoint.
- SupportOps static CSS, DOM textContent rendering and ticket/confirmation interaction can be reused without its caller-supplied auth headers.
- P4 is COMPLETE with local commit 0fcaadc. Initial status, staged and unstaged diff empty.
- Main DB contains the existing P3 ORD-1001 application; browser verification uses existing owned disposable schema/importer.
- Browser must only use Harness same-origin API; local persona switching revokes old session and clears all UI state.
- Analyst snapshot stays separate from operational results. Unknown writes require read-only reconciliation, never automatic replay.

P5 review: a recording error can retain an acknowledged typed domain result; UI now distinguishes this from an unknown write. Real repeated browser faults require per-schema-session backup/metadata paths, preserving historical root artifacts.
