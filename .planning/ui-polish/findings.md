# UI and Jev findings
The current frontend now uses a dark green sidebar, light workspace, responsive cards, role-specific messages and context metric summaries. Every preexisting DOM ID was preserved. Rendering uses textContent; no model output is evaluated as markup. API contracts and business rules are unchanged.
Official Jev sources (2026-10-08):
- https://docs.typesafe.ai/introduction : typed Choice/Score/Noul judgments, not free-form response generation.
- https://docs.typesafe.ai/confidence : confidence is a statistic derived from returned probability distributions; calibrate thresholds on domain data.
- https://docs.typesafe.ai/models : current version jev-1.13.0; English is strongest, other languages including CJK need domain testing; floating aliases can change, pin versions for evaluation.
- https://docs.typesafe.ai/model-jaggedness/jev-1.13 : numeric precision, dates, irrelevant long state, adversarial content and option-order sensitivity are known limitations.
Recommendation: not required for the main path of this local Chinese context-management demonstration. A targeted intent-routing comparison is worthwhile only if it beats the existing classifier/review on independent Chinese holdout data. Memory-candidate scoring can be secondary. Token counts, protected facts, lifecycle/approvals, and loop limits remain deterministic; do not replace BGE reranking without evidence. No Jev API access or paid calls were performed.
Observed UI issue: restored conversation did not synchronize picker selection. Corrected selection on open; browser regression pending.

Final observed result: restored picker synchronized, UI actual stored metrics 3357 -> 2880 /6144 and summaryv2; mobile navigation scrollable without visible scrollbar and no document overflow. Context/summary IDs remain expandable. Browser dead-tab and unavailable body press attempts made no app changes; fresh same-browser tab and documented scrolling recovered. Known model failures remain in docs/validation/workbench.md; not evidence of Jev improvement.
