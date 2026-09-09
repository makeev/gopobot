import asyncio

from .redis_client import close_redis, get_redis


async def check() -> None:
    try:
        if not await get_redis().ping():
            raise RuntimeError("Redis ping failed")
    finally:
        await close_redis()


if __name__ == "__main__":
    asyncio.run(check())
