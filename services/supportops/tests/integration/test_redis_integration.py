import os
from uuid import uuid4

import pytest
from redis import Redis

from app.reliability import RedisReliabilityCoordinator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_REDIS_INTEGRATION") != "1",
        reason="set RUN_REDIS_INTEGRATION=1 to run Redis integration",
    ),
]


def test_real_redis_distributed_lock_and_rate_limit():
    prefix = f"supportops-ci-{uuid4().hex}"
    first = RedisReliabilityCoordinator(
        url="redis://127.0.0.1:6379/0",
        socket_timeout_seconds=1.0,
        key_prefix=prefix,
    )
    second = RedisReliabilityCoordinator(
        url="redis://127.0.0.1:6379/0",
        socket_timeout_seconds=1.0,
        key_prefix=prefix,
    )
    first.ping()

    token = first.acquire_action_lock("ACT-LOCK", 10)
    assert token is not None
    assert second.acquire_action_lock("ACT-LOCK", 10) is None
    first.release_action_lock("ACT-LOCK", token)
    replacement = second.acquire_action_lock("ACT-LOCK", 10)
    assert replacement is not None
    second.release_action_lock("ACT-LOCK", replacement)

    first.register_action_ttl("ACT-TTL", 30)
    raw = Redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
    ttl = raw.ttl(f"{prefix}:action-live:ACT-TTL")
    assert 1 <= ttl <= 30

    one = first.check_rate_limit(
        scope="confirm",
        subject="user-1",
        limit=2,
        window_seconds=30,
    )
    two = second.check_rate_limit(
        scope="confirm",
        subject="user-1",
        limit=2,
        window_seconds=30,
    )
    three = first.check_rate_limit(
        scope="confirm",
        subject="user-1",
        limit=2,
        window_seconds=30,
    )
    assert one.allowed is True
    assert two.allowed is True
    assert three.allowed is False

    for key in raw.scan_iter(f"{prefix}:*"):
        raw.delete(key)
