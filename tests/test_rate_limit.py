from types import SimpleNamespace

from src import rate_limit
from src.rate_limit import RateLimiter


class FakeRedis:
    def __init__(self) -> None:
        self.used = 0
        self.ttl = 0

    async def eval(
        self,
        script: str,
        key_count: int,
        key: str,
        cost: int,
        window: int,
        limit: int,
    ) -> list[int]:
        del script, key_count, key
        if self.used == 0:
            if cost > limit:
                return [0, 0, window]
            self.used = cost
            self.ttl = window
            return [1, self.used, self.ttl]
        if self.used + cost > limit:
            return [0, self.used, self.ttl]
        self.used += cost
        return [1, self.used, self.ttl]


async def test_rate_limit_has_no_off_by_one_and_does_not_charge_rejected_request(
    monkeypatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: redis)
    monkeypatch.setattr(
        rate_limit,
        "settings",
        SimpleNamespace(admin_users=frozenset(), limit_messages=5, limit_time=60),
    )
    limiter = RateLimiter()

    first = await limiter.consume(1, 3)
    rejected = await limiter.consume(1, 3)
    last = await limiter.consume(1, 2)

    assert first.allowed and first.remaining == 2
    assert not rejected.allowed and rejected.used == 3
    assert last.allowed and last.remaining == 0
