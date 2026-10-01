# Phase 7C lexical vs Dense comparison contract v1

Status: frozen setup for Phase 7C-B. No Dense benchmark or real embedding inference was run
while preparing this contract. The contract preserves the Phase 6 corpus and labels.

## One controlled variable

- Lexical baseline: `postgres-fts-simple-cd-v1`; PostgreSQL `simple`, `plainto_tsquery`
  AND matching, `ts_rank_cd(..., 0)`, current revisions, and the existing stable tie-break.
- Dense candidate: `dense-exact-cosine-v1`, evaluation-only and compatible with the existing
  `KnowledgeRetriever` protocol. It does not replace production retrieval or enter Agent wiring.
- Corpus source: Phase 5 ingested PostgreSQL knowledge chunks. Both retrievers run against the
  same isolated database, tenant, namespaces, and latest-revision corpus in one comparison run.
- Dense index: exact in-memory full scan. No vector database, approximate search, fusion, or
  reranking is part of this comparison.
- Scope: SQL selects exact tenant and namespace rows and excludes revisions with a newer
  revision number before vectors are built. The Dense index is bound to that tenant and
  namespace; search rejects a scope mismatch before embedding a query.
- Stable score ordering: cosine descending, then `source_key`, `chunk_index`, `section_path`,
  `content_sha256`. Database UUIDs remain provenance only and do not determine ties.

## Frozen Dense identity

- Adapter: FastEmbed `0.8.1`, local ONNX inference only.
- Model: `BAAI/bge-small-en-v1.5`, dimension `384`.
- FastEmbed model repository: `Qdrant/bge-small-en-v1.5-onnx-Q`; expected ONNX file:
  `model_optimized.onnx`.
- Model artifact revision/hash: unavailable in Phase 7C-A because the model was not downloaded.
  Phase 7C-B must record the local snapshot revision when present. Do not invent a model hash.
- Query prefix: none. Document prefix: none. The pinned FastEmbed model metadata describes
  prefixes as “not so necessary”; the adapter sends the original query and passage text.
- Normalization: the application validates dimension and finite non-zero values, then explicitly
  L2-normalizes both document and query vectors. Similarity is exact cosine similarity.
- Score threshold: none. On a non-empty corpus, Dense returns exactly the nearest `top_k` chunks.
  The six no-answer queries remain in the comparison and may expose abstention loss.

FastEmbed is an optional `dense-eval` dependency group, pinned to `fastembed==0.8.1`; it is not a
default application dependency. Its adapter is imported and initialized only when explicitly used,
and `local_files_only=True` prevents model fallback to a network download. The preflight checks
the package version and expected cache files without importing FastEmbed, reading provider
secrets, creating cache directories, or making a network request. If the model is absent, a real
comparison exits before starting the isolated PostgreSQL runner. Any download requires separate
authorization in a later phase.

## Frozen data and metrics

- Corpus: Phase 6 synthetic corpus, 16 documents, 48 chunks, 48 labeled queries.
- Corpus SHA-256: `1999e21b7b31f164de4c3ff1a0407a509d5a00ffa4ba350be8705b603d2e5369`.
- Dataset `datacopilot-synthetic-v1`, version `1.0.0`, SHA-256:
  `6f1ce5f9d5a7ca9fd9de496dd1249cd037917c86d489ac3377302eee76f70a77`.
- Dataset `supportops-synthetic-v1`, version `1.0.0`, SHA-256:
  `13ce9e620102fa8501ba063f2dc25000b84c0df1568c4c9704174e35b86c1694`.
- K values: `1`, `3`, `5`; comparison request `top_k=5` for both backends.
- Metrics remain the existing source-level `HitRate@K`, `Recall@K`, `MRR@K`, `nDCG@K`, and
  `No-Answer Accuracy@K`. Raw PostgreSQL and cosine scores are not compared numerically.
- All 48 cases run. No category is cherry-picked; paired results use `query_id` and retain the
  categories `exact`, `ordinary`, `acronym`, `paraphrase`, `multi_keyword`, `distractor`, and
  `no_answer`.

## Predeclared Phase 7C-B gates

The answerable set has 42 cases. The material-improvement gate is at least five additional
answerable Hit@5 cases over the lexical baseline (equivalent to an absolute combined HitRate@5
increase of at least `0.10` at this baseline), **and** combined Recall@5 must not decrease,
**and** paraphrase HitRate@5 must be greater than zero. These criteria are fixed before Dense
inference; no score threshold, model, prefix, normalization, or `top_k` tuning is permitted on
these queries.

The lexical no-answer baseline is `6/6`. There is no Dense score threshold. Every no-answer case
remains in the metrics; any no-answer drop is reported, and a drop blocks adoption. If the quality
gate passes while abstention regresses, report `DENSE QUALITY PROMISING BUT ABSTENTION UNSOLVED`.
This comparison cannot adopt Dense into production by itself.

Latency is descriptive, not a pass threshold. Record sequential query latency at the evaluator
boundary, including query embedding and exact ranking. Record model-load, corpus-embedding, and
index-build time separately; do not fold precomputed corpus embedding into “free” work. The Phase
6 lexical combined mean was `4.215 ms` in its measured local environment.

## Future command and artifact

`python scripts/dev.py eval-retrieval-compare --preflight` is metadata/cache-only and must report
`MODEL_AVAILABLE_LOCAL = YES/NO` with `NETWORK_REQUESTS = 0`. The full
`python scripts/dev.py eval-retrieval-compare` command is reserved for the separately authorized
Phase 7C-B local inference run. It checks that the pinned package and model are already local,
creates one run-owned PostgreSQL database, seeds one corpus, runs lexical and Dense, and writes
`.artifacts/phase7c-retrieval-comparison.json`.

The artifact schema records manifest/corpus hashes, Git SHA and dirty state, backend/model
identity, K values, source rankings, existing metrics/category summaries, paired per-case
outcomes, local latency/build timings, and scope/privacy checks. It contains no raw query, raw
retrieved body, or provider secret. Per-case outcomes are `LEXICAL_ONLY_HIT`, `DENSE_ONLY_HIT`,
`BOTH_HIT`, and `BOTH_MISS`; for no-answer cases, “hit” means the result is empty.

## Compatibility evidence

The pinned `fastembed==0.8.1` package metadata accepts Python `>=3.10`. A binary-only `uv pip
compile` resolution succeeded for Python 3.12 and 3.13 on Windows x86_64 and Linux x86_64, with
`onnxruntime==1.30.0`; this is dependency-resolution evidence, not an execution test on those four
platforms. The pinned FastEmbed model catalog identifies the 384-dimensional BGE model and its
ONNX model file. The model itself was neither downloaded nor executed in Phase 7C-A.

References: [FastEmbed 0.8.1 package](https://pypi.org/project/fastembed/0.8.1/),
[pinned BGE model metadata and lazy local-only constructor](https://github.com/qdrant/fastembed/blob/v0.8.1/fastembed/text/onnx_embedding.py),
[pinned query/passage API and `local_files_only` setting](https://github.com/qdrant/fastembed/blob/v0.8.1/fastembed/text/text_embedding_base.py),
[pinned model-cache behavior](https://github.com/qdrant/fastembed/blob/v0.8.1/fastembed/common/model_management.py).
