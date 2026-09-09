from dataclasses import dataclass

from .redis_client import get_redis
from .settings import settings

_CONSUME_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
    if tonumber(ARGV[1]) > tonumber(ARGV[3]) then
        return {0, 0, tonumber(ARGV[2])}
    end
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    return {1, tonumber(ARGV[1]), tonumber(ARGV[2])}
end
local ttl = redis.call('TTL', KEYS[1])
if tonumber(current) + tonumber(ARGV[1]) > tonumber(ARGV[3]) then
    return {0, tonumber(current), ttl}
end
local updated = redis.call('INCRBY', KEYS[1], ARGV[1])
return {1, updated, ttl}
"""


@dataclass(frozen=True)
class LimitResult:
    allowed: bool
    used: int
    remaining: int
    retry_after: int


class RateLimiter:
    async def consume(self, user_id: int, cost: int = 1) -> LimitResult:
        if user_id in settings.admin_users:
            return LimitResult(True, 0, settings.limit_messages, 0)

        allowed, used, ttl = await get_redis().eval(
            _CONSUME_SCRIPT,
            1,
            f"counter:{user_id}",
            cost,
            settings.limit_time,
            settings.limit_messages,
        )
        used = int(used)
        return LimitResult(
            allowed=bool(allowed),
            used=used,
            remaining=max(settings.limit_messages - used, 0),
            retry_after=max(int(ttl), 0),
        )


rate_limiter = RateLimiter()
