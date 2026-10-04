from __future__ import annotations

import os
from dataclasses import dataclass

from app.tenancy import DEMO_TENANT, TenantId


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return int(value)


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite:///./supportops.db"
    database_auto_create_schema: bool = True
    seed_demo_data: bool = False
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    refund_human_review_threshold: float = 500.0

    knowledge_backend: str = "local"
    knowledge_sources_path: str | None = None
    embedding_backend: str = "deterministic"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    embedding_timeout_seconds: float = 5.0
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "supportops_knowledge"
    qdrant_timeout_seconds: float = 3.0
    qdrant_sync_on_start: bool = True
    retrieval_min_evidence_score: float = 0.16
    retrieval_allow_sparse_fallback: bool = True

    reliability_backend: str = "local"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_socket_timeout_seconds: float = 0.5
    redis_key_prefix: str = "supportops"
    reliability_require_startup: bool = True
    reliability_fail_closed_mutations: bool = True
    action_confirmation_ttl_seconds: int = 600
    action_lock_ttl_seconds: int = 15
    rate_limit_window_seconds: int = 60
    support_message_rate_limit: int = 30
    action_confirmation_rate_limit: int = 10
    rate_limit_fail_open: bool = True

    metrics_enabled: bool = True
    tracing_enabled: bool = True
    otel_service_name: str = "supportops-ai"
    otel_exporter_otlp_endpoint: str | None = None
    otel_export_timeout_seconds: float = 3.0

    app_env: str = "production"
    dev_tenant_id: str = DEMO_TENANT
    auth_mode: str = "jwt"
    auth_issuer: str | None = None
    auth_audience: str | None = None
    auth_jwks_url: str | None = None
    auth_algorithm: str = "RS256"

    mcp_auth_mode: str = "jwt"
    mcp_issuer_url: str | None = None
    mcp_resource_server_url: str = "http://127.0.0.1:8001/mcp"
    mcp_required_scope: str = "support:read"
    mcp_write_scope: str = "support:write"
    mcp_dev_customer_id: str = "CUST-001"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./supportops.db"),
            database_auto_create_schema=_env_bool("DATABASE_AUTO_CREATE_SCHEMA", True),
            seed_demo_data=_env_bool("SEED_DEMO_DATA", False),
            llm_base_url=os.getenv("LLM_BASE_URL") or None,
            llm_api_key=os.getenv("LLM_API_KEY") or None,
            llm_model=os.getenv("LLM_MODEL") or None,
            refund_human_review_threshold=float(os.getenv("REFUND_HUMAN_REVIEW_THRESHOLD", "500")),
            knowledge_backend=os.getenv("KNOWLEDGE_BACKEND", "local").lower(),
            knowledge_sources_path=os.getenv("KNOWLEDGE_SOURCES_PATH") or None,
            embedding_backend=os.getenv("EMBEDDING_BACKEND", "deterministic").lower(),
            embedding_base_url=os.getenv("EMBEDDING_BASE_URL") or None,
            embedding_api_key=os.getenv("EMBEDDING_API_KEY") or None,
            embedding_model=os.getenv("EMBEDDING_MODEL") or None,
            embedding_dimension=_env_optional_int("EMBEDDING_DIMENSION"),
            embedding_timeout_seconds=float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "5")),
            qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
            qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
            qdrant_collection=os.getenv("QDRANT_COLLECTION", "supportops_knowledge"),
            qdrant_timeout_seconds=float(os.getenv("QDRANT_TIMEOUT_SECONDS", "3")),
            qdrant_sync_on_start=_env_bool("QDRANT_SYNC_ON_START", True),
            retrieval_min_evidence_score=float(os.getenv("RETRIEVAL_MIN_EVIDENCE_SCORE", "0.16")),
            retrieval_allow_sparse_fallback=_env_bool("RETRIEVAL_ALLOW_SPARSE_FALLBACK", True),
            reliability_backend=os.getenv("RELIABILITY_BACKEND", "local").lower(),
            redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            redis_socket_timeout_seconds=float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "0.5")),
            redis_key_prefix=os.getenv("REDIS_KEY_PREFIX", "supportops"),
            reliability_require_startup=_env_bool("RELIABILITY_REQUIRE_STARTUP", True),
            reliability_fail_closed_mutations=_env_bool("RELIABILITY_FAIL_CLOSED_MUTATIONS", True),
            action_confirmation_ttl_seconds=int(
                os.getenv("ACTION_CONFIRMATION_TTL_SECONDS", "600")
            ),
            action_lock_ttl_seconds=int(os.getenv("ACTION_LOCK_TTL_SECONDS", "15")),
            rate_limit_window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
            support_message_rate_limit=int(os.getenv("SUPPORT_MESSAGE_RATE_LIMIT", "30")),
            action_confirmation_rate_limit=int(os.getenv("ACTION_CONFIRMATION_RATE_LIMIT", "10")),
            rate_limit_fail_open=_env_bool("RATE_LIMIT_FAIL_OPEN", True),
            metrics_enabled=_env_bool("METRICS_ENABLED", True),
            tracing_enabled=_env_bool("TRACING_ENABLED", True),
            otel_service_name=os.getenv("OTEL_SERVICE_NAME", "supportops-ai"),
            otel_exporter_otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            otel_export_timeout_seconds=float(os.getenv("OTEL_EXPORT_TIMEOUT_SECONDS", "3")),
            app_env=os.getenv("APP_ENV", "production").lower(),
            dev_tenant_id=os.getenv("DEV_TENANT_ID", DEMO_TENANT),
            auth_mode=os.getenv("AUTH_MODE", "jwt").lower(),
            auth_issuer=os.getenv("AUTH_ISSUER") or None,
            auth_audience=os.getenv("AUTH_AUDIENCE") or None,
            auth_jwks_url=os.getenv("AUTH_JWKS_URL") or None,
            auth_algorithm=os.getenv("AUTH_ALGORITHM", "RS256"),
            mcp_auth_mode=os.getenv("MCP_AUTH_MODE", "jwt").lower(),
            mcp_issuer_url=os.getenv("MCP_ISSUER_URL") or None,
            mcp_resource_server_url=os.getenv(
                "MCP_RESOURCE_SERVER_URL", "http://127.0.0.1:8001/mcp"
            ),
            mcp_required_scope=os.getenv("MCP_REQUIRED_SCOPE", "support:read"),
            mcp_write_scope=os.getenv("MCP_WRITE_SCOPE", "support:write"),
            mcp_dev_customer_id=os.getenv("MCP_DEV_CUSTOMER_ID", "CUST-001"),
        )

    def validate_security(self) -> None:
        if self.app_env not in {"development", "production"}:
            raise ValueError("unsupported_app_environment")
        if self.auth_mode not in {"dev", "jwt"}:
            raise ValueError("unsupported_auth_mode")
        if self.mcp_auth_mode not in {"disabled", "jwt"}:
            raise ValueError("unsupported_mcp_auth_mode")
        if self.app_env == "production" and (
            self.auth_mode != "jwt" or self.mcp_auth_mode != "jwt"
        ):
            raise ValueError("production_requires_jwt_auth")
        if self.app_env == "production" and self.seed_demo_data:
            raise ValueError("production_demo_seed_forbidden")
        if self.auth_mode == "jwt" or self.mcp_auth_mode == "jwt":
            if not (self.auth_issuer and self.auth_audience and self.auth_jwks_url):
                raise ValueError("jwt_auth_not_configured")
            if self.auth_algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}:
                raise ValueError("unsupported_jwt_algorithm")
        if self.auth_mode == "dev" or self.mcp_auth_mode == "disabled":
            TenantId(self.dev_tenant_id)
        if (
            not self.mcp_required_scope.strip()
            or not self.mcp_write_scope.strip()
            or self.mcp_required_scope == self.mcp_write_scope
        ):
            raise ValueError("distinct_mcp_read_write_scopes_required")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def production_embeddings_enabled(self) -> bool:
        return self.embedding_backend == "openai" and bool(
            self.embedding_base_url and self.embedding_model
        )
