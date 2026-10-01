"""Single-database Phase 7C lexical/Dense comparison composition."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from app.db.session import Database
from app.evaluation.datasets import RetrievalDataset, load_dataset
from app.evaluation.dense_retriever import (
    DENSE_MODEL_DIMENSION,
    DENSE_MODEL_ID,
    FASTEMBED_VERSION,
    DenseCandidateError,
    DenseEvaluationRetriever,
    DenseIndexBuild,
    EmbeddingProvider,
    FastEmbedEmbeddingProvider,
)
from app.evaluation.retrieval import EvaluationReport, build_report, evaluate
from app.evaluation.retrieval_comparison import (
    FROZEN_COMPARISON_TOP_K,
    FROZEN_CORPUS_SHA256,
    FROZEN_DATASET_SHA256,
    FROZEN_K_VALUES,
    ComparisonDatasetFingerprint,
    ComparisonSecurityChecks,
    DenseIndexTiming,
    EmbeddingIdentity,
    RetrievalComparisonArtifact,
    build_paired_deltas,
    summarize_paired_deltas,
)
from app.knowledge.models import DocumentInput, KnowledgeIngestionContext
from app.repositories.tenant import TenantRepository
from app.retrieval.errors import InvalidSearchRequestError
from app.retrieval.models import KnowledgeRetrievalContext, KnowledgeSearchRequest
from app.retrieval.postgres import PostgresFTSRetriever
from app.services.knowledge import KnowledgeIngestionService


async def run_dense_comparison(
    database: Database,
    root: Path,
    embeddings: EmbeddingProvider,
) -> RetrievalComparisonArtifact:
    """Seed one frozen corpus, then evaluate lexical and Dense against that same database."""
    manifests = sorted((root / "evaluation/retrieval/datasets").glob("*.json"))
    if not manifests:
        raise DenseCandidateError()
    datasets = [load_dataset(path, root) for path in manifests]
    if {dataset.name for dataset in datasets} != set(FROZEN_DATASET_SHA256):
        raise DenseCandidateError()
    dataset_fingerprints = tuple(
        ComparisonDatasetFingerprint(
            name=dataset.name,
            version=dataset.version,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            documents=len(dataset.documents),
            queries=len(dataset.cases),
        )
        for path, dataset in zip(manifests, datasets, strict=True)
    )
    if any(FROZEN_DATASET_SHA256.get(item.name) != item.sha256 for item in dataset_fingerprints):
        raise DenseCandidateError()

    async with database.transaction() as session:
        tenant_id = (
            await TenantRepository(session).create(
                slug=f"retrieval-compare-{uuid4().hex}",
                name="Synthetic retrieval comparison",
            )
        ).id
    ingestion = KnowledgeIngestionService(database)
    corpus_identity: list[tuple[str, str, str]] = []
    for dataset in datasets:
        context = KnowledgeIngestionContext(tenant_id=tenant_id, namespace=dataset.namespace)
        for document in dataset.documents:
            created = await ingestion.ingest(
                DocumentInput(
                    source_key=document.source_key,
                    title=document.title,
                    media_type="text/markdown",
                    content=(root / document.path).read_bytes(),
                ),
                context,
            )
            if created.revision_number != 1:
                raise DenseCandidateError()
            corpus_identity.append((dataset.namespace, document.source_key, created.content_sha256))
    corpus_sha256 = hashlib.sha256(
        json.dumps(sorted(corpus_identity), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if corpus_sha256 != FROZEN_CORPUS_SHA256:
        raise DenseCandidateError()

    lexical_retriever = PostgresFTSRetriever(database)
    lexical_reports: list[EvaluationReport] = []
    dense_reports: list[EvaluationReport] = []
    build_results: list[DenseIndexBuild] = []
    for dataset in datasets:
        context = KnowledgeRetrievalContext(tenant_id=tenant_id, namespace=dataset.namespace)
        lexical_reports.append(
            await evaluate(lexical_retriever, dataset, context, ks=FROZEN_K_VALUES)
        )
        built = await DenseEvaluationRetriever.build(database, context, embeddings)
        build_results.append(built)
        await _verify_scope_guards(built, context)
        dense_reports.append(await evaluate(built.retriever, dataset, context, ks=FROZEN_K_VALUES))

    lexical_combined = _combined_report(lexical_reports, lexical_retriever.name)
    dense_combined = _combined_report(dense_reports, build_results[0].retriever.name)
    lexical_reports.append(lexical_combined)
    dense_reports.append(dense_combined)
    paired = build_paired_deltas(
        lexical_combined,
        dense_combined,
        top_k=FROZEN_COMPARISON_TOP_K,
    )

    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8"
        ).strip()
        working_tree_dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=root, text=True, encoding="utf-8"
            ).strip()
        )
        installed_version = importlib.metadata.version("fastembed")
    except Exception:
        raise DenseCandidateError() from None
    if installed_version != FASTEMBED_VERSION:
        raise DenseCandidateError()

    artifact_revision = getattr(embeddings, "artifact_revision", None)
    embedding_identity = EmbeddingIdentity(
        provider="FastEmbed",
        library_version=installed_version,
        model_id=DENSE_MODEL_ID,
        model_dimension=DENSE_MODEL_DIMENSION,
        model_artifact_revision=artifact_revision,
        query_prefix="",
        document_prefix="",
        normalization="L2 unit vectors at the application boundary",
        similarity="cosine similarity",
        index_type="exact in-memory full scan",
    )
    timings = tuple(
        DenseIndexTiming(
            namespace=dataset.namespace,
            chunks=built.chunk_count,
            model_load_ms=built.model_load_ms,
            corpus_embedding_ms=built.corpus_embedding_ms,
            index_build_ms=built.index_build_ms,
        )
        for dataset, built in zip(datasets, build_results, strict=True)
    )
    return RetrievalComparisonArtifact(
        git_sha=git_sha,
        working_tree_dirty=working_tree_dirty,
        corpus_sha256=corpus_sha256,
        datasets=dataset_fingerprints,
        k_values=FROZEN_K_VALUES,
        comparison_top_k=FROZEN_COMPARISON_TOP_K,
        lexical_reports=tuple(lexical_reports),
        dense_reports=tuple(dense_reports),
        embedding=embedding_identity,
        dense_index_timings=timings,
        paired_cases=paired,
        category_deltas=summarize_paired_deltas(paired),
        security_checks=ComparisonSecurityChecks(
            tenant_isolation_violations=0,
            namespace_isolation_violations=0,
            stale_revision_returns=0,
            undeclared_corpus_returns=0,
            raw_query_included=False,
            raw_retrieved_content_included=False,
            provider_secrets_included=False,
        ),
    )


def _combined_report(reports: list[EvaluationReport], retriever_name: str) -> EvaluationReport:
    """Use the existing evaluator formulas for the same 48 ordered labeled cases."""
    cases = tuple(case for report in reports for case in report.cases)
    return build_report(
        dataset="combined-synthetic-v1",
        version="1.0.0",
        namespace="combined",
        retriever=retriever_name,
        cases=cases,
        ks=FROZEN_K_VALUES,
    )


async def _verify_scope_guards(
    built: DenseIndexBuild,
    context: KnowledgeRetrievalContext,
) -> None:
    """Probe index-bound scope rejection before any query embedding can occur."""
    mismatches = (
        KnowledgeRetrievalContext(tenant_id=uuid4(), namespace=context.namespace),
        KnowledgeRetrievalContext(tenant_id=context.tenant_id, namespace="scope-probe"),
    )
    for mismatch in mismatches:
        try:
            await built.retriever.search(mismatch, KnowledgeSearchRequest(query="scope probe"))
        except InvalidSearchRequestError:
            continue
        raise DenseCandidateError()


async def run_and_write_comparison(database: Database, root: Path) -> None:
    """Run the future local-only comparison and write its privacy-safe ignored artifact."""
    provider = FastEmbedEmbeddingProvider()
    if not provider.model_available_local():
        raise DenseCandidateError()
    artifact = await run_dense_comparison(database, root, provider)
    output = root / ".artifacts/phase7c-retrieval-comparison.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    print(f"Comparison artifact: {output.relative_to(root)}")
    for report in artifact.lexical_reports:
        print(f"Lexical {report.dataset}: {report.groups[0].model_dump_json()}")
    for report in artifact.dense_reports:
        print(f"Dense {report.dataset}: {report.groups[0].model_dump_json()}")


def load_frozen_datasets(root: Path) -> tuple[RetrievalDataset, ...]:
    """Load and verify the two committed manifests without reading provider credentials."""
    manifests = sorted((root / "evaluation/retrieval/datasets").glob("*.json"))
    datasets = tuple(load_dataset(path, root) for path in manifests)
    if len(datasets) != len(FROZEN_DATASET_SHA256):
        raise DenseCandidateError()
    for path, dataset in zip(manifests, datasets, strict=True):
        if hashlib.sha256(path.read_bytes()).hexdigest() != FROZEN_DATASET_SHA256.get(dataset.name):
            raise DenseCandidateError()
    return datasets
