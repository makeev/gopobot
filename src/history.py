import json
from dataclasses import dataclass

from .redis_client import get_redis
from .settings import settings

RESET_PHRASES = frozenset(
    {
        "сброс",
        "сброс истории",
        "забудь всё",
        "забудь все",
        "очисти историю",
        "reset",
        "clear history",
        "new conversation",
        "новая тема",
        "новый разговор",
    }
)


@dataclass(frozen=True)
class MemoryInfo:
    messages: int
    has_summary: bool
    ttl_seconds: int


class MessageHistory:
    def _history_key(self, conversation_id: str) -> str:
        return f"history:{conversation_id}"

    def _summary_key(self, conversation_id: str) -> str:
        return f"history-summary:{conversation_id}"

    def _legacy_key(self, conversation_id: str) -> str | None:
        parts = conversation_id.split(":")
        if len(parts) == 3 and parts[0] == parts[1]:
            return f"history:{parts[1]}"
        return None

    async def add_message(self, conversation_id: str, role: str, content: str) -> None:
        redis = get_redis()
        key = self._history_key(conversation_id)
        message = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        async with redis.pipeline(transaction=True) as pipe:
            pipe.lpush(key, message)
            pipe.ltrim(key, 0, settings.max_history_messages - 1)
            pipe.expire(key, settings.history_ttl)
            pipe.expire(self._summary_key(conversation_id), settings.history_ttl)
            await pipe.execute()

    async def get_messages(self, conversation_id: str) -> list[dict[str, str]]:
        redis = get_redis()
        key = self._history_key(conversation_id)
        messages = await redis.lrange(key, 0, -1)
        legacy_key = self._legacy_key(conversation_id)
        if not messages and legacy_key and await redis.exists(legacy_key):
            if await redis.renamenx(legacy_key, key):
                await redis.ltrim(key, 0, settings.max_history_messages - 1)
                await redis.expire(key, settings.history_ttl)
            messages = await redis.lrange(key, 0, -1)
        result: list[dict[str, str]] = []
        for message in reversed(messages):
            try:
                decoded = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                continue
            if decoded.get("role") in {"user", "assistant"} and isinstance(
                decoded.get("content"),
                str,
            ):
                result.append(decoded)
        return result

    async def replace_messages(self, conversation_id: str, messages: list[dict[str, str]]) -> None:
        redis = get_redis()
        key = self._history_key(conversation_id)
        async with redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            for message in messages:
                pipe.lpush(key, json.dumps(message, ensure_ascii=False))
            if messages:
                pipe.expire(key, settings.history_ttl)
            await pipe.execute()

    async def get_summary(self, conversation_id: str) -> str | None:
        return await get_redis().get(self._summary_key(conversation_id))

    async def set_summary(self, conversation_id: str, summary: str) -> None:
        await get_redis().setex(self._summary_key(conversation_id), settings.history_ttl, summary)

    async def clear_history(self, conversation_id: str) -> None:
        keys = [self._history_key(conversation_id), self._summary_key(conversation_id)]
        legacy_key = self._legacy_key(conversation_id)
        if legacy_key:
            keys.append(legacy_key)
        else:
            parts = conversation_id.split(":")
            if len(parts) == 3:
                keys.append(f"history:{parts[1]}")
        await get_redis().delete(*keys)

    async def expire_legacy_history(self) -> int:
        redis = get_redis()
        updated = 0
        async for key in redis.scan_iter(match="history:*"):
            suffix = key.removeprefix("history:")
            if ":" not in suffix and await redis.ttl(key) < 0:
                await redis.expire(key, settings.history_ttl)
                updated += 1
        return updated

    async def get_memory_info(self, conversation_id: str) -> MemoryInfo:
        await self.get_messages(conversation_id)
        redis = get_redis()
        history_key = self._history_key(conversation_id)
        summary_key = self._summary_key(conversation_id)
        async with redis.pipeline(transaction=False) as pipe:
            pipe.llen(history_key)
            pipe.exists(summary_key)
            pipe.ttl(history_key)
            count, summary_exists, ttl = await pipe.execute()
        return MemoryInfo(
            messages=int(count),
            has_summary=bool(summary_exists),
            ttl_seconds=max(int(ttl), 0),
        )

    async def get_last_user_message(self, conversation_id: str) -> str | None:
        for message in reversed(await self.get_messages(conversation_id)):
            if message["role"] == "user":
                return message["content"]
        return None

    async def get_last_assistant_message(self, conversation_id: str) -> str | None:
        for message in reversed(await self.get_messages(conversation_id)):
            if message["role"] == "assistant":
                return message["content"]
        return None

    @staticmethod
    def should_reset_history(message_content: str) -> bool:
        normalized = " ".join(message_content.lower().strip().split())
        return normalized in RESET_PHRASES


history_manager = MessageHistory()
