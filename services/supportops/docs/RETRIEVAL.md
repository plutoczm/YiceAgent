# Retrieval: operating contract and limitations

This document keeps local regression machinery, production-capable adapters, index lifecycle and degradation semantics explicit.

## Components

| Stage | Local / CI | Production-capable adapter |
|---|---|---|
| Knowledge source | versioned bundled JSON | same contract; external publisher not implemented yet |
| Chunking | deterministic stable chunk IDs | same contract |
| Sparse candidate generation | BM25 in process | BM25 in process |
| Dense representation | deterministic hashed vector | OpenAI-compatible embedding endpoint |
| Dense storage/search | in-memory vectors | Qdrant named dense vector |
| Candidate fusion | weighted RRF | weighted RRF |
| Second-stage ranking | deterministic score-aware reranker | same reranker |
| Answerability | evidence threshold | same contract; threshold must be revalidated on new corpora |
| Grounding | version/chunk/source citations + abstention | same contract |
| Failure behavior | typed failure + configurable sparse fallback | same contract |

The deterministic hashed vector is **not a semantic embedding model**. It exists for reproducible tests and local development.

## Knowledge source and chunk lineage

Each active source record provides:

```text
document_id
version
title
text
source_uri
```

The deterministic chunker emits `RetrievalDocument` records with stable `chunk_id` and a SHA-256 `content_hash`. For example:

```text
KB-REFUND-01:1:0000
```

Citation payloads carry document ID, version, chunk ID and source URI. Knowledge-search audit records also keep chunk/version lineage.

The current JSON source is checked into/package-bundled with the application. It is not yet a remote content-management or approval pipeline.

## Local deterministic mode

```bash
pip install -e ".[dev]"
python -m evals.run_retrieval_evals
```

```text
KNOWLEDGE_BACKEND=local
EMBEDDING_BACKEND=deterministic
RETRIEVAL_MIN_EVIDENCE_SCORE=0.16
```

The fixed benchmark has 50 supported and 10 unsupported queries. Tokenization, corpus, fusion, reranking and answerability changes must pass the same gates rather than weakening the threshold to recover failures.

## Qdrant dense mode

```bash
pip install -e ".[rag]"
docker compose --profile rag up -d qdrant
```

```text
KNOWLEDGE_BACKEND=qdrant
EMBEDDING_BACKEND=openai
EMBEDDING_BASE_URL=https://embedding-provider.example/v1
EMBEDDING_API_KEY=...
EMBEDDING_MODEL=...
EMBEDDING_DIMENSION=...
EMBEDDING_TIMEOUT_SECONDS=5
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=supportops_knowledge
QDRANT_TIMEOUT_SECONDS=3
QDRANT_SYNC_ON_START=true
RETRIEVAL_ALLOW_SPARSE_FALLBACK=true
```

The application rejects `qdrant + deterministic` because the deterministic representation is test/local-only. Qdrant stores the dense leg; sparse retrieval remains application-side BM25.

## Incremental synchronization

Remote Qdrant point payloads store:

- stable index/chunk ID;
- document ID/version/source URI;
- content hash.

At synchronization time:

```text
desired chunk absent remotely      -> embed + upsert
same index_id and same content hash -> unchanged, no embed/upsert
same index_id and changed hash      -> embed + upsert changed chunk only
remote index_id absent from desired -> delete stale vector
```

The adapter reports `IndexSyncStats` with desired, embedded, upserted, deleted and unchanged counts.

This is intentionally content-addressed. A process restart with an unchanged knowledge set should not re-embed the complete corpus.

## Failure taxonomy

Current typed failure reasons include:

- `embedding_timeout`
- `embedding_rate_limited`
- `embedding_provider_error`
- `embedding_invalid_response`
- `qdrant_unavailable`
- `qdrant_index_error`

Provider exceptions are translated at the adapter boundary so the application can make an explicit availability decision without parsing vendor-specific exceptions.

## Sparse fallback versus fail-fast

With:

```text
RETRIEVAL_ALLOW_SPARSE_FALLBACK=true
```

a typed dense startup/search failure leaves the sparse BM25 path available. The search/response is marked degraded and includes a typed degradation reason; the audit outcome is also `degraded`.

The answerability threshold is **not reduced** during degraded retrieval. Sparse fallback may preserve service availability, but it does not guarantee equivalent recall or semantic quality.

With fallback disabled, the same typed error propagates. Startup/index failure therefore prevents startup rather than silently changing the configured retrieval architecture.

## Real Qdrant integration CI

The workflow starts Qdrant v1.18.2 and executes the optional adapter against it. A local HTTP embedding server implements the OpenAI-compatible `/embeddings` protocol using a deterministic test representation.

Current integration checks:

- first-time collection creation and two-chunk upsert;
- unchanged resync with zero re-embedding/upsert;
- changed-only re-embedding/upsert;
- stale-vector deletion;
- dense query against the real Qdrant process;
- typed query-outage behavior after successful sync.

Separate regular tests cover embedding timeout, runtime sparse degradation, startup sparse degradation and strict fail-fast mode.

This validates the adapter protocol and index lifecycle. It is **not** an external embedding-model quality benchmark and does not test Qdrant clustering/HA.

## Why sparse retrieval is still application-side

Moving BM25/sparse vectors into Qdrant is not automatically an improvement. The current architecture has a stable sparse baseline and independent dense adapter. A future Qdrant-native sparse implementation should be accepted only with a measurable retrieval/latency/operational benefit under the same evaluation set.

## Known limitations

- Knowledge publication is source-controlled JSON rather than an authenticated admin ingestion/approval service.
- Startup sync is single-process orchestration, not a distributed indexing coordinator or background queue.
- There is no object-store connector, parser pipeline or asynchronous large-corpus indexing job.
- The deterministic 60-case benchmark is small and curated.
- Production semantic quality for a chosen external embedding model has not been measured.
- Qdrant CI is single-node and does not validate replication/failover.
- The reranker is deterministic, not a learned cross-encoder.
- Citation presence is evaluated, but claim-level faithfulness and multi-document citation precision/recall are not yet measured.
- Retrieval latency/SLO telemetry is not yet exported through OpenTelemetry/Prometheus.

## Next acceptance criteria

The next retrieval-specific improvements should prioritize:

1. a larger held-out corpus/query set separated from ranking calibration;
2. claim-level citation precision/faithfulness for multi-document answers;
3. retrieval-stage latency metrics and P95 SLOs;
4. external knowledge publication/version-approval flow or asynchronous ingestion for larger corpora;
5. outage/retry/backoff experiments for a selected production embedding provider;
6. a learned reranker only after an A/B report shows quality gain worth its latency/cost.
