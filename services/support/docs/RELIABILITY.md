# Reliability after S2b

The current business-operation boundary uses PostgreSQL order/operation row locks,
semantic uniqueness, version checks and atomic local mutation/receipt/audit/state.
Redis remains a request-limiting adapter; legacy action lease/TTL settings do not
gate S2b submission. See [OPERATIONS.md](OPERATIONS.md) for current recovery,
concurrency and replay semantics.

The following historical notes describe the earlier Redis-gated design.
Their lock-busy and fragmented-mutation behavior has been superseded by S2b.

## Historical distributed reliability contract

This document defines what Redis does, what PostgreSQL does, and what the project does **not** claim.

## State ownership

PostgreSQL remains the durable source of truth. A pending action stores its kind/payload/status plus `created_at` and `expires_at`; executed mutations also have an action receipt keyed by the action ID. Orders, tickets and audit events are durable database state.

Redis is a coordination/control-plane dependency only. It stores short-lived action locks, TTL mirrors and rate-limit counters. Losing Redis keys must not rewrite the durable business meaning of an existing action.

## Confirmation expiry

`ACTION_CONFIRMATION_TTL_SECONDS` is persisted into PostgreSQL as an absolute `expires_at`. Redis receives a TTL mirror, but confirmation always checks the database deadline under the action lock. Therefore a Redis restart cannot extend or shorten the actual business confirmation window.

Expired actions transition to `EXPIRED` and cannot invoke the refund/return tool.

## Cross-replica action lock

Redis acquisition uses `SET key token NX PX lease_ms`. The token is random per acquisition. Release uses Lua:

```text
if GET(key) == token:
    DEL(key)
```

This prevents an old owner from deleting a newer lease after its own lease has expired.

The confirm flow re-reads the pending action after acquiring the lease before evaluating terminal state, expiry or executing a tool. A busy lease maps to HTTP 409 rather than waiting indefinitely inside the request.

## Mutation outage policy

With `RELIABILITY_FAIL_CLOSED_MUTATIONS=true`, a Redis failure while acquiring the coordination lock or refreshing the pre-mutation TTL aborts confirmation before the business side effect. This makes distributed coordination a required safety dependency for mutating requests in Redis deployment mode.

Cleanup/release failures after a successfully persisted mutation are audited as degradation and do not pretend the already committed side effect was rolled back.

## Rate limiting

The Redis rate limiter is a fixed-window counter implemented atomically with Lua (`INCR`, first-request `EXPIRE`, `TTL`). Scope and authenticated principal subject form the key. Exceeded limits return HTTP 429 with `Retry-After`.

`RATE_LIMIT_FAIL_OPEN=true` is deliberately independent from mutation fail-closed policy. If the limiter backend is unavailable, support/read traffic may continue; operators can set it false for stricter deployments.

### Historical reliability notes observability

The reliability coordinator is wrapped by the v0.6 observability layer. `ping`, action TTL set/clear, action lock acquire/release and rate-limit decisions emit OpenTelemetry operation spans plus bounded Prometheus operation counters/histograms. Typed reliability exceptions also increment `supportops_degradation_total{component="reliability",reason=...}`.

Action IDs, principal IDs and Redis keys are intentionally not Prometheus labels. Durable action/audit state remains the business evidence source; telemetry only measures runtime behavior.

## Local mode

`RELIABILITY_BACKEND=local` provides the same interface using process-local locks/counters. It is intended for deterministic local/unit testing and **does not coordinate multiple workers or replicas**.

## What this protects

The current design reduces the risk of two API replicas concurrently resolving the same pending action from the same stale state. It also makes confirmation expiry durable and rate limiting shared when Redis is enabled.

## What this does not prove

### No exactly-once claim

The Redis lease has a fixed TTL and no renewal/heartbeat. If a synchronous downstream mutation exceeds the lease duration, a second replica may eventually acquire it. For this reason choose a lease longer than expected mutation latency, propagate the stable action ID as downstream idempotency key, keep the durable application receipt, require a real external provider to honor idempotency, and add lease renewal or a durable job/outbox workflow if mutations become long-running.

### No Redis HA claim

CI uses one standalone Redis 7.4 service. Sentinel, Cluster, failover, network-partition and replica-promotion semantics are not tested.

### Fixed-window rate limiting

The limiter is fixed-window, not sliding-window/token-bucket. It is adequate as a bounded engineering baseline but has the usual boundary burst behavior.

## Current verification

The v0.6 successful code-head CI run keeps the expiry/no-side-effect, lock-busy, fail-closed mutation and 429/Retry-After unit contracts green. A separate real-Redis integration still verifies cross-coordinator lock exclusion/release, Redis TTL presence and shared atomic rate-limit state. The new observability regression also checks that reliability/dependency failures can be represented as typed degradation metrics without introducing high-cardinality business identifiers into Prometheus.

Future reliability work should be driven by observed requirements: lease renewal/outbox for longer asynchronous operations and Redis HA testing when deployment topology requires it. The v0.6 metrics/traces should provide the evidence needed before changing lease duration or limiter algorithms.
