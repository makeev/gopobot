from types import SimpleNamespace

import fakeredis.aioredis

from src import history
from src.history import MessageHistory


async def test_history_preserves_order_compacts_and_clears(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(history, "get_redis", lambda: redis)
    monkeypatch.setattr(
        history,
        "settings",
        SimpleNamespace(max_history_messages=3, history_ttl=300),
    )
    manager = MessageHistory()
    conversation = "100:200:0"

    await manager.add_message(conversation, "user", "один")
    await manager.add_message(conversation, "assistant", "два")
    await manager.add_message(conversation, "user", "три")
    await manager.add_message(conversation, "assistant", "четыре")

    assert [item["content"] for item in await manager.get_messages(conversation)] == [
        "два",
        "три",
        "четыре",
    ]

    await manager.set_summary(conversation, "резюме")
    info = await manager.get_memory_info(conversation)
    assert info.messages == 3
    assert info.has_summary
    assert info.ttl_seconds > 0

    await manager.clear_history(conversation)
    assert await manager.get_messages(conversation) == []
    assert await manager.get_summary(conversation) is None
    await redis.aclose()


async def test_private_chat_migrates_legacy_history(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(history, "get_redis", lambda: redis)
    monkeypatch.setattr(
        history,
        "settings",
        SimpleNamespace(max_history_messages=10, history_ttl=300),
    )
    await redis.lpush("history:7", '{"role": "user", "content": "старый контекст"}')

    messages = await MessageHistory().get_messages("7:7:0")

    assert messages == [{"role": "user", "content": "старый контекст"}]
    assert not await redis.exists("history:7")
    assert await redis.ttl("history:7:7:0") > 0
    await redis.aclose()


async def test_old_group_history_receives_retention_period(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(history, "get_redis", lambda: redis)
    monkeypatch.setattr(
        history,
        "settings",
        SimpleNamespace(max_history_messages=10, history_ttl=300),
    )
    await redis.lpush("history:8", '{"role": "user", "content": "старый контекст"}')

    updated = await MessageHistory().expire_legacy_history()

    assert updated == 1
    assert await redis.ttl("history:8") > 0
    await redis.aclose()
