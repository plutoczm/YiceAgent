import hashlib

import pytest
from pydantic import ValidationError

from app.evaluation.datasets import QueryCategory
from app.evaluation.retrieval import CaseRun, EvaluationReport, RankedSource, build_report
from app.evaluation.retrieval_comparison import (
    CategoryPairedDelta,
    ComparisonDatasetFingerprint,
    ComparisonSecurityChecks,
    DenseIndexTiming,
    EmbeddingIdentity,
    PairedOutcome,
    RetrievalComparisonArtifact,
    build_paired_deltas,
    summarize_paired_deltas,
)


def source(key: str) -> RankedSource:
    return RankedSource(
        source_key=key,
        section_path=("Policy",),
        chunk_index=0,
        content_sha256=hashlib.sha256(key.encode()).hexdigest(),
    )


def case(
    query_id: str,
    category: QueryCategory,
    relevant: tuple[str, ...],
    ranking: tuple[RankedSource, ...],
) -> CaseRun:
    return CaseRun(
        query_id=query_id,
        category=category,
        relevant_sources=relevant,
        ranking=ranking,
        duration_ms=1.0,
    )


def report(retriever: str, cases: tuple[CaseRun, ...]) -> EvaluationReport:
    return build_report(
        dataset="comparison-fixture",
        version="1.0.0",
        namespace="supportops",
        retriever=retriever,
        cases=cases,
        ks=(1, 3, 5),
    )


def test_paired_deltas_cover_answerable_and_no_answer_cases() -> None:
    lexical_cases = (
        case("lexical-hit", QueryCategory.EXACT, ("expected.md",), (source("expected.md"),)),
        case("negative", QueryCategory.NO_ANSWER, (), ()),
        case("dense-hit", QueryCategory.PARAPHRASE, ("expected.md",), (source("other.md"),)),
        case("both-miss", QueryCategory.ORDINARY, ("expected.md",), ()),
    )
    dense_cases = (
        case("lexical-hit", QueryCategory.EXACT, ("expected.md",), (source("other.md"),)),
        case("negative", QueryCategory.NO_ANSWER, (), (source("nearest.md"),)),
        case("dense-hit", QueryCategory.PARAPHRASE, ("expected.md",), (source("expected.md"),)),
        case("both-miss", QueryCategory.ORDINARY, ("expected.md",), (source("other.md"),)),
    )
    paired = build_paired_deltas(report("lexical", lexical_cases), report("dense", dense_cases))
    assert [item.outcome for item in paired] == [
        PairedOutcome.LEXICAL_ONLY_HIT,
        PairedOutcome.LEXICAL_ONLY_HIT,
        PairedOutcome.DENSE_ONLY_HIT,
        PairedOutcome.BOTH_MISS,
    ]
    assert paired[0].lexical_first_relevant_rank == 1
    assert paired[0].dense_first_relevant_rank is None
    assert paired[1].lexical_first_relevant_rank is None
    categories = {item.category: item for item in summarize_paired_deltas(paired)}
    assert categories["paraphrase"].dense_only_hits == 1
    assert categories["no_answer"].lexical_only_hits == 1
    assert sum(item.cases for item in categories.values()) == 4


def test_paired_reports_must_share_case_identity_and_labels() -> None:
    lexical = report(
        "lexical",
        (case("case-a", QueryCategory.EXACT, ("expected.md",), (source("expected.md"),)),),
    )
    different_labels = report(
        "dense",
        (case("case-a", QueryCategory.EXACT, ("other.md",), (source("other.md"),)),),
    )
    with pytest.raises(ValueError, match="labels changed"):
        build_paired_deltas(lexical, different_labels)
    with pytest.raises(ValidationError):
        CategoryPairedDelta(
            category="exact",
            cases=2,
            lexical_only_hits=1,
            dense_only_hits=0,
            both_hits=0,
            both_misses=0,
        )


def test_comparison_artifact_contains_rankings_but_no_raw_payloads() -> None:
    lexical_cases = (
        case("case-a", QueryCategory.EXACT, ("expected.md",), (source("expected.md"),)),
    )
    dense_cases = (case("case-a", QueryCategory.EXACT, ("expected.md",), (source("expected.md"),)),)
    lexical = report("postgres-fts-simple-cd-v1", lexical_cases)
    dense = report("dense-exact-cosine-v1:fake", dense_cases)
    paired = build_paired_deltas(lexical, dense)
    artifact = RetrievalComparisonArtifact(
        git_sha="a" * 40,
        working_tree_dirty=False,
        corpus_sha256="b" * 64,
        datasets=(
            ComparisonDatasetFingerprint(
                name="supportops-synthetic-v1",
                version="1.0.0",
                sha256="c" * 64,
                documents=8,
                queries=24,
            ),
        ),
        lexical_reports=(lexical,),
        dense_reports=(dense,),
        embedding=EmbeddingIdentity(
            provider="FastEmbed",
            library_version="0.8.1",
            model_id="BAAI/bge-small-en-v1.5",
            model_dimension=384,
            model_artifact_revision=None,
            query_prefix="",
            document_prefix="",
            normalization="L2 unit vectors",
            similarity="cosine",
            index_type="exact in-memory full scan",
        ),
        dense_index_timings=(
            DenseIndexTiming(
                namespace="supportops",
                chunks=24,
                model_load_ms=0.0,
                corpus_embedding_ms=1.0,
                index_build_ms=0.1,
            ),
        ),
        paired_cases=paired,
        category_deltas=summarize_paired_deltas(paired),
        security_checks=ComparisonSecurityChecks(
            tenant_isolation_violations=0,
            namespace_isolation_violations=0,
            stale_revision_returns=0,
            undeclared_corpus_returns=0,
        ),
    )
    serialized = artifact.model_dump_json()
    assert "expected.md" in serialized
    assert '"query":' not in serialized
    assert '"content":' not in serialized
    with pytest.raises(ValidationError):
        type(paired[0]).model_validate({**paired[0].model_dump(), "query": "raw prompt"})
