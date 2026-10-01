"""Privacy-safe paired result schema for the frozen Phase 7C retrieval comparison."""

from collections import defaultdict
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.evaluation.datasets import QueryCategory
from app.evaluation.retrieval import CaseRun, EvaluationReport
from app.knowledge.models import KnowledgeModel

FROZEN_CORPUS_SHA256 = "1999e21b7b31f164de4c3ff1a0407a509d5a00ffa4ba350be8705b603d2e5369"
FROZEN_DATASET_SHA256 = {
    "datacopilot-synthetic-v1": "6f1ce5f9d5a7ca9fd9de496dd1249cd037917c86d489ac3377302eee76f70a77",
    "supportops-synthetic-v1": "13ce9e620102fa8501ba063f2dc25000b84c0df1568c4c9704174e35b86c1694",
}
FROZEN_K_VALUES = (1, 3, 5)
FROZEN_COMPARISON_TOP_K = 5


class PairedOutcome(StrEnum):
    """Per-case correctness delta; no-answer correctness means an empty top-k result."""

    LEXICAL_ONLY_HIT = "LEXICAL_ONLY_HIT"
    DENSE_ONLY_HIT = "DENSE_ONLY_HIT"
    BOTH_HIT = "BOTH_HIT"
    BOTH_MISS = "BOTH_MISS"


class ComparisonDatasetFingerprint(KnowledgeModel):
    """Frozen manifest identity and counts without query text or retrieved content."""

    name: str
    version: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    documents: int = Field(ge=1)
    queries: int = Field(ge=1)


class PairedCaseDelta(KnowledgeModel):
    """One privacy-safe paired result linked only by stable query ID and category."""

    query_id: str
    category: QueryCategory
    outcome: PairedOutcome
    lexical_first_relevant_rank: int | None = Field(default=None, ge=1)
    dense_first_relevant_rank: int | None = Field(default=None, ge=1)


class CategoryPairedDelta(KnowledgeModel):
    """Counts of paired outcomes for a fixed dataset category."""

    category: str
    cases: int = Field(ge=1)
    lexical_only_hits: int = Field(ge=0)
    dense_only_hits: int = Field(ge=0)
    both_hits: int = Field(ge=0)
    both_misses: int = Field(ge=0)

    @model_validator(mode="after")
    def complete_count(self) -> Self:
        """Require every paired query in this category to have exactly one outcome."""
        if (
            self.lexical_only_hits + self.dense_only_hits + self.both_hits + self.both_misses
            != self.cases
        ):
            raise ValueError("Invalid paired category counts")
        return self


class EmbeddingIdentity(KnowledgeModel):
    """Model and adapter metadata frozen before the measured comparison."""

    provider: str
    library_version: str
    model_id: str
    model_dimension: int = Field(gt=0)
    model_artifact_revision: str | None
    query_prefix: str
    document_prefix: str
    normalization: str
    similarity: str
    index_type: str


class DenseIndexTiming(KnowledgeModel):
    """Offline build timings; each cost is separate from evaluator query latency."""

    namespace: str
    chunks: int = Field(ge=1)
    model_load_ms: float = Field(ge=0, allow_inf_nan=False)
    corpus_embedding_ms: float = Field(ge=0, allow_inf_nan=False)
    index_build_ms: float = Field(ge=0, allow_inf_nan=False)


class ComparisonSecurityChecks(KnowledgeModel):
    """Scope and privacy checks recorded in the generated comparison artifact."""

    tenant_isolation_violations: int = Field(ge=0)
    namespace_isolation_violations: int = Field(ge=0)
    stale_revision_returns: int = Field(ge=0)
    undeclared_corpus_returns: int = Field(ge=0)
    raw_query_included: bool = False
    raw_retrieved_content_included: bool = False
    provider_secrets_included: bool = False


class RetrievalComparisonArtifact(KnowledgeModel):
    """Future one-run lexical/Dense evidence with no user prompts or chunk bodies."""

    schema_version: Literal["phase7c-retrieval-comparison-v1"] = "phase7c-retrieval-comparison-v1"
    git_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    working_tree_dirty: bool
    corpus_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    datasets: tuple[ComparisonDatasetFingerprint, ...]
    k_values: tuple[int, ...] = FROZEN_K_VALUES
    comparison_top_k: int = FROZEN_COMPARISON_TOP_K
    lexical_reports: tuple[EvaluationReport, ...]
    dense_reports: tuple[EvaluationReport, ...]
    embedding: EmbeddingIdentity
    dense_index_timings: tuple[DenseIndexTiming, ...]
    paired_cases: tuple[PairedCaseDelta, ...]
    category_deltas: tuple[CategoryPairedDelta, ...]
    security_checks: ComparisonSecurityChecks

    @model_validator(mode="after")
    def safe_frozen_artifact(self) -> Self:
        """Require the frozen K boundary and reject any report containing raw payloads."""
        if self.k_values != FROZEN_K_VALUES or self.comparison_top_k != FROZEN_COMPARISON_TOP_K:
            raise ValueError("Comparison contract drift")
        if len({case.query_id for case in self.paired_cases}) != len(self.paired_cases):
            raise ValueError("Duplicate paired query_id")
        checks = self.security_checks
        if (
            checks.raw_query_included
            or checks.raw_retrieved_content_included
            or checks.provider_secrets_included
        ):
            raise ValueError("Comparison artifact contains private input")
        return self


def _first_relevant_rank(case: CaseRun, top_k: int) -> int | None:
    """Return the first relevant source rank using only stable evaluator DTO fields."""
    for rank, source in enumerate(case.ranking[:top_k], 1):
        if source.source_key in case.relevant_sources:
            return rank
    return None


def build_paired_deltas(
    lexical: EvaluationReport,
    dense: EvaluationReport,
    *,
    top_k: int = FROZEN_COMPARISON_TOP_K,
) -> tuple[PairedCaseDelta, ...]:
    """Pair two unchanged evaluator reports by query ID without copying query text."""
    if (
        not 1 <= top_k <= 20
        or lexical.dataset != dense.dataset
        or lexical.dataset_version != dense.dataset_version
        or lexical.namespace != dense.namespace
        or lexical.query_count != dense.query_count
    ):
        raise ValueError("Incompatible retrieval reports")
    lexical_by_id = {case.query_id: case for case in lexical.cases}
    dense_by_id = {case.query_id: case for case in dense.cases}
    if not lexical_by_id or lexical_by_id.keys() != dense_by_id.keys():
        raise ValueError("Incompatible retrieval cases")
    result: list[PairedCaseDelta] = []
    for lexical_case in lexical.cases:
        dense_case = dense_by_id[lexical_case.query_id]
        if (
            lexical_case.category is not dense_case.category
            or lexical_case.relevant_sources != dense_case.relevant_sources
        ):
            raise ValueError("Retrieval case labels changed")
        lexical_rank = _first_relevant_rank(lexical_case, top_k)
        dense_rank = _first_relevant_rank(dense_case, top_k)
        if not lexical_case.relevant_sources:
            lexical_correct = not lexical_case.ranking[:top_k]
            dense_correct = not dense_case.ranking[:top_k]
        else:
            lexical_correct = lexical_rank is not None
            dense_correct = dense_rank is not None
        if lexical_correct and dense_correct:
            outcome = PairedOutcome.BOTH_HIT
        elif lexical_correct:
            outcome = PairedOutcome.LEXICAL_ONLY_HIT
        elif dense_correct:
            outcome = PairedOutcome.DENSE_ONLY_HIT
        else:
            outcome = PairedOutcome.BOTH_MISS
        result.append(
            PairedCaseDelta(
                query_id=lexical_case.query_id,
                category=lexical_case.category,
                outcome=outcome,
                lexical_first_relevant_rank=lexical_rank,
                dense_first_relevant_rank=dense_rank,
            )
        )
    return tuple(result)


def summarize_paired_deltas(
    deltas: tuple[PairedCaseDelta, ...],
) -> tuple[CategoryPairedDelta, ...]:
    """Aggregate every frozen category, including the no-answer subgroup."""
    grouped: dict[str, list[PairedOutcome]] = defaultdict(list)
    for delta in deltas:
        grouped[delta.category.value].append(delta.outcome)
    return tuple(
        CategoryPairedDelta(
            category=category,
            cases=len(outcomes),
            lexical_only_hits=outcomes.count(PairedOutcome.LEXICAL_ONLY_HIT),
            dense_only_hits=outcomes.count(PairedOutcome.DENSE_ONLY_HIT),
            both_hits=outcomes.count(PairedOutcome.BOTH_HIT),
            both_misses=outcomes.count(PairedOutcome.BOTH_MISS),
        )
        for category, outcomes in sorted(grouped.items())
    )
