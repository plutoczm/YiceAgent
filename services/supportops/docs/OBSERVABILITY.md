# Observability and SLO contract

SupportOps AI uses observability to make application behavior measurable without turning telemetry into a second business database or leaking high-cardinality customer data into metrics.

## Audit is not APM

The project keeps two separate evidence planes:

```text
Durable audit event
  actor_id / business operation / resource / decision / outcome / trace_id
  -> answers: who did what, to which business resource, and what was persisted?

OpenTelemetry span
  parent/child causality / operation / backend / outcome / duration / exception
  -> answers: where did this request spend time or fail?

Prometheus metric
  bounded aggregate dimensions
  -> answers: how often, how slow, how many failures/degradations?
```

A trace is not treated as the durable mutation record. The PostgreSQL audit/receipt/action state remains authoritative for business review and retry semantics.

## Span model

Representative request paths produce nested application-boundary spans such as:

```text
http.server.request
  +-- reliability.rate_limit
  +-- agent.route
  +-- retrieval.hybrid.search
  |     +-- retrieval.dense.search
  |           +-- embedding.embed
  +-- policy.refund / policy.return
  +-- reliability.action_ttl.set / clear
  +-- reliability.action_lock.acquire / release
  +-- tool.order.get / tool.refund.quote / tool.refund.execute
      / tool.return.execute / tool.ticket.create
```

The exact child set depends on intent. Qdrant startup synchronization is instrumented separately as `retrieval.qdrant.startup_sync`.

Incoming HTTP requests accept W3C `traceparent` through the Trace Context propagator. Trace context is used for causality; the trace ID is deliberately not copied into a Prometheus label.

## OTLP configuration

Tracing can run locally without an exporter. To export spans through OTLP/HTTP:

```text
TRACING_ENABLED=true
OTEL_SERVICE_NAME=supportops-ai
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces
OTEL_EXPORT_TIMEOUT_SECONDS=3
```

`OTEL_EXPORTER_OTLP_ENDPOINT` is interpreted as the full traces endpoint consumed by the configured OTLP/HTTP exporter. The repository does not bundle an OpenTelemetry Collector and does not prescribe Jaeger, Tempo, Honeycomb or another backend.

## Prometheus metric contract

When `METRICS_ENABLED=true`, `/metrics` exports a process-local registry containing:

```text
supportops_http_requests_total{method,route,status_class}
supportops_http_request_duration_seconds{method,route}

supportops_operation_total{component,operation,backend,outcome}
supportops_operation_duration_seconds{component,operation,backend}

supportops_degradation_total{component,reason}
```

Request and operation latency use explicit histogram buckets from 1 ms through 10 seconds.

### Cardinality rule

Metrics labels must come from bounded vocabularies. Current permitted dimensions are route template, HTTP method/status class, component, operation, backend, outcome and typed degradation reason.

The following must **not** become Prometheus labels:

```text
customer_id
principal/user ID
order_id
action_id
ticket_id
conversation_id
trace_id
raw query/message text
arbitrary model/provider error text
```

These values can create unbounded series cardinality or leak sensitive context. Business identifiers belong in durable audit records or traces subject to the deployment's telemetry policy.

## Example PromQL

HTTP P95 by route:

```promql
histogram_quantile(
  0.95,
  sum by (le, route) (
    rate(supportops_http_request_duration_seconds_bucket[5m])
  )
)
```

Operation P95:

```promql
histogram_quantile(
  0.95,
  sum by (le, operation) (
    rate(supportops_operation_duration_seconds_bucket[5m])
  )
)
```

HTTP 5xx ratio:

```promql
sum(rate(supportops_http_requests_total{status_class="5xx"}[5m]))
/
sum(rate(supportops_http_requests_total[5m]))
```

Typed degradation rate:

```promql
sum by (component, reason) (
  rate(supportops_degradation_total[5m])
)
```

These queries are examples for an external Prometheus deployment. Alert thresholds are not hard-coded because valid production thresholds depend on actual traffic, dependencies and deployment objectives.

## Deterministic CI latency regression

`python -m evals.run_slo_smoke` runs 60 local HTTP requests: 30 knowledge/policy requests and 30 order-status requests. It uses TestClient, SQLite and deterministic local retrieval so the pull-request gate is reproducible and does not depend on a paid model or network service.

Latest verified code-head result:

```text
requests: 60
success_rate: 1.0
error_count: 0
degraded_responses: 0
http_p50_ms: 5.652
http_p95_ms: 8.508
knowledge_p95_ms: 8.924
order_p95_ms: 6.745
```

The CI job also checks that HTTP/operation histograms, the degradation counter and router/retrieval/tool operation metrics are present. The current local P95 guard is deliberately loose at 750 ms; it is a catastrophic-regression guard rather than a production service-level objective.

## What is not claimed

- The CI latency values are **not** production P50/P95 and do not include networked PostgreSQL, Redis, Qdrant, an external LLM or an external embedding endpoint.
- The repository does not bundle Prometheus, Grafana or an OpenTelemetry Collector.
- Each API process exposes an in-process Prometheus registry. Multi-replica aggregation, scrape discovery and retention are responsibilities of the deployment observability stack.
- Instrumentation currently targets application boundaries; it does not automatically create every SQLAlchemy, HTTP client or Redis library span.
- Token/cost metrics are not yet reported as a production contract because provider usage/cost accounting is not instrumented end-to-end.
- Trace sampling, exporter retry/durability and collector HA are not production-hardened by this repository.
- No production alert/SLO threshold is claimed until real deployment traffic establishes a baseline.

## Next measurement layer

The next useful evaluation is held-out end-to-end conversation/tool execution with observability attached: tool-selection and argument accuracy, task completion, escalation recall, hallucinated-action rate, multi-turn mutation safety and claim-level citation faithfulness. Production latency/error/degradation SLOs should then be derived from real scraped/exported traffic rather than copied from the deterministic CI workload.
