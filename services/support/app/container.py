from __future__ import annotations

from dataclasses import dataclass

from app.auth import AuthService
from app.config import Settings
from app.guardrails import InputGuardrails
from app.knowledge import KnowledgeService, default_knowledge_documents
from app.model_gateway import OpenAICompatibleModel
from app.observability import (
    Observability,
    ObservedDenseRetriever,
    ObservedEmbeddingProvider,
    ObservedHybridRetriever,
    ObservedPolicy,
    ObservedReliabilityCoordinator,
    ObservedRouter,
    ObservedTools,
)
from app.operations import BusinessOperations
from app.orchestrator import SupportOrchestrator
from app.policy import ActionPolicy
from app.reliability import LocalReliabilityCoordinator, RedisReliabilityCoordinator
from app.retrieval import (
    BM25Retriever,
    DeterministicHashEmbedding,
    HybridRetriever,
    InMemoryDenseRetriever,
    OpenAICompatibleEmbedding,
    QdrantDenseRetriever,
    RetrievalBackendError,
    UnavailableDenseRetriever,
)
from app.router import IntentRouter
from app.store import SupportStore
from app.tickets import TicketWorkflow
from app.tools import SupportTools
from app.workflows import SupportWorkflows


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    observability: Observability
    auth: AuthService
    store: SupportStore
    reliability: ObservedReliabilityCoordinator
    tickets: TicketWorkflow
    tools: ObservedTools
    knowledge: KnowledgeService
    orchestrator: SupportOrchestrator
    operations: BusinessOperations


def build_container(settings: Settings | None = None) -> ServiceContainer:
    settings = settings or Settings.from_env()
    settings.validate_security()
    observability = Observability(settings)
    store = SupportStore(settings.database_url)
    if settings.database_auto_create_schema:
        store.create_schema()
    if settings.seed_demo_data:
        store.seed_demo_data(tenant_id=settings.dev_tenant_id)

    if settings.reliability_backend == "local":
        reliability_delegate = LocalReliabilityCoordinator()
    elif settings.reliability_backend == "redis":
        reliability_delegate = RedisReliabilityCoordinator(
            url=settings.redis_url,
            socket_timeout_seconds=settings.redis_socket_timeout_seconds,
            key_prefix=settings.redis_key_prefix,
        )
    else:
        raise ValueError("RELIABILITY_BACKEND must be 'local' or 'redis'")
    reliability = ObservedReliabilityCoordinator(reliability_delegate, observability)
    if settings.reliability_backend == "redis" and settings.reliability_require_startup:
        reliability.ping()

    model = None
    if settings.llm_enabled:
        model = OpenAICompatibleModel(
            base_url=settings.llm_base_url or "",
            model=settings.llm_model or "",
            api_key=settings.llm_api_key,
        )

    documents = default_knowledge_documents(settings.knowledge_sources_path)
    if settings.embedding_backend == "deterministic":
        embedding_delegate = DeterministicHashEmbedding()
        embedding_backend = "deterministic"
    elif settings.production_embeddings_enabled:
        embedding_delegate = OpenAICompatibleEmbedding(
            base_url=settings.embedding_base_url or "",
            model=settings.embedding_model or "",
            api_key=settings.embedding_api_key,
            dimension=settings.embedding_dimension,
            timeout_seconds=settings.embedding_timeout_seconds,
        )
        embedding_backend = "openai-compatible"
    else:
        raise ValueError("EMBEDDING_BACKEND=openai requires EMBEDDING_BASE_URL and EMBEDDING_MODEL")
    embedding_provider = ObservedEmbeddingProvider(
        embedding_delegate,
        observability,
        backend=embedding_backend,
    )

    try:
        if settings.knowledge_backend == "local":
            dense_delegate = InMemoryDenseRetriever(documents, embedding_provider)
            dense_backend = "memory"
        elif settings.knowledge_backend == "qdrant":
            if settings.embedding_backend != "openai":
                raise ValueError(
                    "KNOWLEDGE_BACKEND=qdrant requires EMBEDDING_BACKEND=openai; "
                    "the deterministic embedding is CI/local-only"
                )
            with observability.operation(
                "retrieval.qdrant.startup_sync",
                component="retrieval",
                backend="qdrant",
            ):
                dense_delegate = QdrantDenseRetriever(
                    documents,
                    embedding_provider,
                    url=settings.qdrant_url,
                    api_key=settings.qdrant_api_key,
                    collection=settings.qdrant_collection,
                    timeout_seconds=settings.qdrant_timeout_seconds,
                    sync_on_start=settings.qdrant_sync_on_start,
                )
            dense_backend = "qdrant"
        else:
            raise ValueError("KNOWLEDGE_BACKEND must be 'local' or 'qdrant'")
    except RetrievalBackendError as exc:
        if not settings.retrieval_allow_sparse_fallback:
            raise
        dense_delegate = UnavailableDenseRetriever(exc)
        dense_backend = "unavailable"

    dense_retriever = ObservedDenseRetriever(
        dense_delegate,
        observability,
        backend=dense_backend,
    )
    retriever_delegate = HybridRetriever(
        documents,
        dense_retriever=dense_retriever,
        sparse_retriever=BM25Retriever(documents),
        allow_sparse_fallback=settings.retrieval_allow_sparse_fallback,
    )
    retriever = ObservedHybridRetriever(retriever_delegate, observability)

    auth = AuthService(settings)
    guardrails = InputGuardrails()
    router = ObservedRouter(IntentRouter(model=model), observability)
    knowledge = KnowledgeService(
        model=model,
        documents=documents,
        retriever=retriever,
        min_evidence_score=settings.retrieval_min_evidence_score,
    )
    policy = ObservedPolicy(
        ActionPolicy(settings.refund_human_review_threshold),
        observability,
    )
    tickets = TicketWorkflow(store)
    workflows = SupportWorkflows(
        store=store,
        tickets=tickets,
        policy=policy,
        reliability=reliability,
        action_confirmation_ttl_seconds=settings.action_confirmation_ttl_seconds,
    )
    tools = ObservedTools(SupportTools(workflows), observability)
    orchestrator = SupportOrchestrator(
        store=store,
        guardrails=guardrails,
        router=router,
        knowledge=knowledge,
        tools=tools,
    )
    return ServiceContainer(
        settings=settings,
        observability=observability,
        auth=auth,
        store=store,
        reliability=reliability,
        tickets=tickets,
        tools=tools,
        knowledge=knowledge,
        orchestrator=orchestrator,
        operations=workflows.operations,
    )
