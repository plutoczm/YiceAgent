from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

from app.config import Settings


@dataclass(slots=True)
class OperationObservation:
    span: Span
    outcome: str
    backend: str

    def set_outcome(self, outcome: str) -> None:
        self.outcome = outcome

    def set_backend(self, backend: str) -> None:
        self.backend = backend

    def set_attribute(self, key: str, value: object | None) -> None:
        if value is None:
            return
        if isinstance(value, (bool, int, float, str)):
            self.span.set_attribute(key, value)
            return
        self.span.set_attribute(key, str(value))


class Observability:
    """Low-cardinality metrics plus OpenTelemetry spans for application operations."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = CollectorRegistry()
        self._operation_total = Counter(
            "supportops_operation_total",
            "Completed SupportOps operations.",
            ("component", "operation", "backend", "outcome"),
            registry=self.registry,
        )
        self._operation_duration = Histogram(
            "supportops_operation_duration_seconds",
            "SupportOps operation latency.",
            ("component", "operation", "backend"),
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry,
        )
        self._degradation_total = Counter(
            "supportops_degradation_total",
            "Explicit dependency/application degradations.",
            ("component", "reason"),
            registry=self.registry,
        )
        self._http_total = Counter(
            "supportops_http_requests_total",
            "HTTP requests by route template and status class.",
            ("method", "route", "status_class"),
            registry=self.registry,
        )
        self._http_duration = Histogram(
            "supportops_http_request_duration_seconds",
            "HTTP request latency by route template.",
            ("method", "route"),
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry,
        )
        self._propagator = TraceContextTextMapPropagator()
        self._tracer_provider: TracerProvider | None = None
        if settings.tracing_enabled:
            provider = TracerProvider(
                resource=Resource.create(
                    {
                        "service.name": settings.otel_service_name,
                        "service.version": "0.6.0",
                    }
                )
            )
            if settings.otel_exporter_otlp_endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                exporter = OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    timeout=settings.otel_export_timeout_seconds,
                )
                provider.add_span_processor(BatchSpanProcessor(exporter))
            self._tracer_provider = provider
            self.tracer = provider.get_tracer(settings.otel_service_name, "0.6.0")
        else:
            self.tracer = trace.NoOpTracerProvider().get_tracer(settings.otel_service_name)

    @contextmanager
    def operation(
        self,
        name: str,
        *,
        component: str,
        backend: str = "internal",
        context: Any | None = None,
    ) -> Iterator[OperationObservation]:
        started = perf_counter()
        with self.tracer.start_as_current_span(name, context=context) as span:
            observation = OperationObservation(span=span, outcome="success", backend=backend)
            span.set_attribute("supportops.component", component)
            span.set_attribute("supportops.operation", name)
            try:
                yield observation
            except Exception as exc:
                observation.outcome = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                reason = getattr(exc, "reason", None)
                if reason is not None and component in {"reliability", "retrieval"}:
                    self.record_degradation(component, str(reason))
                raise
            finally:
                duration = max(0.0, perf_counter() - started)
                span.set_attribute("supportops.outcome", observation.outcome)
                span.set_attribute("supportops.backend", observation.backend)
                span.set_attribute("supportops.duration_ms", round(duration * 1000.0, 3))
                if self.settings.metrics_enabled:
                    self._operation_total.labels(
                        component=component,
                        operation=name,
                        backend=observation.backend,
                        outcome=observation.outcome,
                    ).inc()
                    self._operation_duration.labels(
                        component=component,
                        operation=name,
                        backend=observation.backend,
                    ).observe(duration)

    def record_degradation(self, component: str, reason: str) -> None:
        if self.settings.metrics_enabled:
            self._degradation_total.labels(component=component, reason=reason).inc()

    def record_http(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        if not self.settings.metrics_enabled:
            return
        status_class = f"{max(1, status_code // 100)}xx"
        self._http_total.labels(
            method=method,
            route=route,
            status_class=status_class,
        ).inc()
        self._http_duration.labels(method=method, route=route).observe(max(0.0, duration_seconds))

    def extract_context(self, headers: Mapping[str, str]) -> Any | None:
        if not self.settings.tracing_enabled:
            return None
        return self._propagator.extract(carrier=dict(headers))

    def render_metrics(self) -> bytes:
        return generate_latest(self.registry)

    @property
    def metrics_content_type(self) -> str:
        return CONTENT_TYPE_LATEST

    def shutdown(self) -> None:
        if self._tracer_provider is not None:
            self._tracer_provider.shutdown()


class ObservedRouter:
    def __init__(self, delegate: Any, observability: Observability) -> None:
        self._delegate = delegate
        self._observability = observability

    def route(self, message: str) -> Any:
        with self._observability.operation(
            "agent.route",
            component="agent",
            backend="router",
        ) as observation:
            route = self._delegate.route(message)
            observation.set_backend(str(getattr(route, "source", "router")))
            intent = getattr(getattr(route, "intent", None), "value", None)
            observation.set_attribute("supportops.intent", intent)
            return route

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ObservedPolicy:
    def __init__(self, delegate: Any, observability: Observability) -> None:
        self._delegate = delegate
        self._observability = observability

    def refund(self, amount: float) -> Any:
        with self._observability.operation(
            "policy.refund",
            component="policy",
            backend="deterministic",
        ) as observation:
            decision = self._delegate.refund(amount)
            action = getattr(getattr(decision, "action", None), "value", "success")
            observation.set_outcome(str(action))
            return decision

    def return_request(self) -> Any:
        with self._observability.operation(
            "policy.return",
            component="policy",
            backend="deterministic",
        ) as observation:
            decision = self._delegate.return_request()
            action = getattr(getattr(decision, "action", None), "value", "success")
            observation.set_outcome(str(action))
            return decision

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ObservedEmbeddingProvider:
    def __init__(
        self,
        delegate: Any,
        observability: Observability,
        *,
        backend: str,
    ) -> None:
        self._delegate = delegate
        self._observability = observability
        self._backend = backend

    @property
    def dimension(self) -> int:
        return self._delegate.dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._observability.operation(
            "embedding.embed",
            component="retrieval",
            backend=self._backend,
        ) as observation:
            observation.set_attribute("supportops.embedding.batch_size", len(texts))
            return self._delegate.embed(texts)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ObservedDenseRetriever:
    def __init__(
        self,
        delegate: Any,
        observability: Observability,
        *,
        backend: str,
    ) -> None:
        self._delegate = delegate
        self._observability = observability
        self._backend = backend

    def search(self, query: str, *, limit: int) -> Any:
        with self._observability.operation(
            "retrieval.dense.search",
            component="retrieval",
            backend=self._backend,
        ) as observation:
            observation.set_attribute("supportops.retrieval.limit", limit)
            return self._delegate.search(query, limit=limit)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ObservedHybridRetriever:
    def __init__(self, delegate: Any, observability: Observability) -> None:
        self._delegate = delegate
        self._observability = observability

    def search(self, query: str, *, limit: int = 3, prefetch: int = 10) -> Any:
        return self.search_with_status(query, limit=limit, prefetch=prefetch).hits

    def search_with_status(
        self,
        query: str,
        *,
        limit: int = 3,
        prefetch: int = 10,
    ) -> Any:
        with self._observability.operation(
            "retrieval.hybrid.search",
            component="retrieval",
            backend="hybrid",
        ) as observation:
            result = self._delegate.search_with_status(
                query,
                limit=limit,
                prefetch=prefetch,
            )
            observation.set_attribute("supportops.retrieval.hit_count", len(result.hits))
            if result.degraded:
                observation.set_outcome("degraded")
                reason = result.degradation_reason or "unknown"
                observation.set_attribute("supportops.degradation_reason", reason)
            return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ObservedReliabilityCoordinator:
    def __init__(self, delegate: Any, observability: Observability) -> None:
        self._delegate = delegate
        self._observability = observability
        self.backend_name = str(delegate.backend_name)

    def ping(self) -> None:
        with self._observability.operation(
            "reliability.ping",
            component="reliability",
            backend=self.backend_name,
        ):
            self._delegate.ping()

    def register_action_ttl(self, action_id: str, ttl_seconds: int) -> None:
        with self._observability.operation(
            "reliability.action_ttl.set",
            component="reliability",
            backend=self.backend_name,
        ) as observation:
            observation.set_attribute("supportops.ttl_seconds", ttl_seconds)
            self._delegate.register_action_ttl(action_id, ttl_seconds)

    def clear_action_ttl(self, action_id: str) -> None:
        with self._observability.operation(
            "reliability.action_ttl.clear",
            component="reliability",
            backend=self.backend_name,
        ):
            self._delegate.clear_action_ttl(action_id)

    def acquire_action_lock(self, action_id: str, ttl_seconds: int) -> str | None:
        with self._observability.operation(
            "reliability.action_lock.acquire",
            component="reliability",
            backend=self.backend_name,
        ) as observation:
            observation.set_attribute("supportops.lock_ttl_seconds", ttl_seconds)
            token = self._delegate.acquire_action_lock(action_id, ttl_seconds)
            if token is None:
                observation.set_outcome("busy")
            return token

    def release_action_lock(self, action_id: str, token: str) -> None:
        with self._observability.operation(
            "reliability.action_lock.release",
            component="reliability",
            backend=self.backend_name,
        ):
            self._delegate.release_action_lock(action_id, token)

    def check_rate_limit(
        self,
        *,
        scope: str,
        subject: str,
        limit: int,
        window_seconds: int,
    ) -> Any:
        with self._observability.operation(
            "reliability.rate_limit",
            component="reliability",
            backend=self.backend_name,
        ) as observation:
            observation.set_attribute("supportops.rate_limit.scope", scope)
            decision = self._delegate.check_rate_limit(
                scope=scope,
                subject=subject,
                limit=limit,
                window_seconds=window_seconds,
            )
            if not decision.allowed:
                observation.set_outcome("limited")
            return decision

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ObservedTools:
    def __init__(self, delegate: Any, observability: Observability) -> None:
        self._delegate = delegate
        self._observability = observability

    def get_order(self, **kwargs: Any) -> Any:
        with self._observability.operation(
            "tool.order.get",
            component="tool",
            backend="sqlalchemy",
        ) as observation:
            result = self._delegate.get_order(**kwargs)
            if result is None:
                observation.set_outcome("not_found")
            return result

    def prepare_refund(self, **kwargs: Any) -> Any:
        return self._invoke("tool.refund.prepare", self._delegate.prepare_refund, kwargs)

    def prepare_return(self, **kwargs: Any) -> Any:
        return self._invoke("tool.return.prepare", self._delegate.prepare_return, kwargs)

    def submit_confirmed_action(self, **kwargs: Any) -> Any:
        return self._invoke(
            "tool.action.submit",
            self._delegate.submit_confirmed_action,
            kwargs,
        )

    def resolve_action(self, **kwargs: Any) -> Any:
        return self._invoke("tool.operation.resolve", self._delegate.resolve_action, kwargs)

    def create_ticket(self, **kwargs: Any) -> Any:
        return self._invoke("tool.ticket.create", self._delegate.create_ticket, kwargs)

    def _invoke(self, operation: str, func: Any, kwargs: dict[str, Any]) -> Any:
        with self._observability.operation(
            operation,
            component="tool",
            backend="sqlalchemy",
        ):
            return func(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)
