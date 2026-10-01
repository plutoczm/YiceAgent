from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError


class ReliabilityBackendError(RuntimeError):
    def __init__(self, reason: str, *, operation: str) -> None:
        self.reason = reason
        self.operation = operation
        super().__init__(f"{reason}:{operation}")


class MutationLockBusy(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class LocalReliabilityCoordinator:
    backend_name = "local"

    def __init__(self) -> None:
        self._mutex = Lock()
        self._action_ttls: dict[str, float] = {}
        self._action_locks: dict[str, tuple[str, float]] = {}
        self._rate_limits: dict[str, tuple[int, float]] = {}

    def ping(self) -> None:
        return None

    def register_action_ttl(self, action_id: str, ttl_seconds: int) -> None:
        with self._mutex:
            self._action_ttls[action_id] = monotonic() + max(1, ttl_seconds)

    def clear_action_ttl(self, action_id: str) -> None:
        with self._mutex:
            self._action_ttls.pop(action_id, None)

    def acquire_action_lock(self, action_id: str, ttl_seconds: int) -> str | None:
        now = monotonic()
        token = uuid4().hex
        with self._mutex:
            current = self._action_locks.get(action_id)
            if current is not None and current[1] > now:
                return None
            self._action_locks[action_id] = (token, now + max(1, ttl_seconds))
        return token

    def release_action_lock(self, action_id: str, token: str) -> None:
        with self._mutex:
            current = self._action_locks.get(action_id)
            if current is not None and current[0] == token:
                self._action_locks.pop(action_id, None)

    def check_rate_limit(
        self,
        *,
        scope: str,
        subject: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        if limit <= 0:
            return RateLimitDecision(True, limit, 0, 0)
        key = f"{scope}:{subject}"
        now = monotonic()
        window = max(1, window_seconds)
        with self._mutex:
            count, reset_at = self._rate_limits.get(key, (0, now + window))
            if reset_at <= now:
                count, reset_at = 0, now + window
            count += 1
            self._rate_limits[key] = (count, reset_at)
            retry_after = max(1, int(reset_at - now))
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after_seconds=retry_after,
        )


class RedisReliabilityCoordinator:
    backend_name = "redis"

    _RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

    _RATE_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""

    def __init__(
        self,
        *,
        url: str,
        socket_timeout_seconds: float,
        key_prefix: str = "supportops",
    ) -> None:
        self._key_prefix = key_prefix.strip(":") or "supportops"
        self._client = Redis.from_url(
            url,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=socket_timeout_seconds,
            decode_responses=True,
        )

    def ping(self) -> None:
        self._call("redis.ping", self._client.ping)

    def register_action_ttl(self, action_id: str, ttl_seconds: int) -> None:
        self._call(
            "redis.action_ttl.set",
            self._client.set,
            self._key("action-live", action_id),
            "1",
            ex=max(1, ttl_seconds),
        )

    def clear_action_ttl(self, action_id: str) -> None:
        self._call(
            "redis.action_ttl.delete",
            self._client.delete,
            self._key("action-live", action_id),
        )

    def acquire_action_lock(self, action_id: str, ttl_seconds: int) -> str | None:
        token = uuid4().hex
        acquired = self._call(
            "redis.action_lock.acquire",
            self._client.set,
            self._key("action-lock", action_id),
            token,
            nx=True,
            px=max(1, ttl_seconds) * 1000,
        )
        return token if acquired else None

    def release_action_lock(self, action_id: str, token: str) -> None:
        self._call(
            "redis.action_lock.release",
            self._client.eval,
            self._RELEASE_LOCK_SCRIPT,
            1,
            self._key("action-lock", action_id),
            token,
        )

    def check_rate_limit(
        self,
        *,
        scope: str,
        subject: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        if limit <= 0:
            return RateLimitDecision(True, limit, 0, 0)
        response = self._call(
            "redis.rate_limit",
            self._client.eval,
            self._RATE_LIMIT_SCRIPT,
            1,
            self._key("rate", scope, subject),
            max(1, window_seconds),
        )
        count = int(response[0])
        ttl = max(1, int(response[1]))
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after_seconds=ttl,
        )

    def _key(self, *parts: str) -> str:
        safe = [part.replace(":", "_") for part in parts]
        return ":".join([self._key_prefix, *safe])

    @staticmethod
    def _call(operation: str, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except RedisError as exc:
            raise ReliabilityBackendError("redis_unavailable", operation=operation) from exc
