import json
from typing import List, Dict, Optional

import redis.asyncio as redis

import settings


class MessageHistory:
    def __init__(self):
        self.redis_client = None

    async def get_redis(self):
        if not self.redis_client:
            self.redis_client = await redis.from_url(settings.REDIS_URL)
        return self.redis_client

    def _get_history_key(self, user_id: int) -> str:
        return f"history:{user_id}"

    async def add_message(self, user_id: int, role: str, content: str):
        r = await self.get_redis()
        history_key = self._get_history_key(user_id)

        message = {
            "role": role,
            "content": content
        }

        await r.lpush(history_key, json.dumps(message, ensure_ascii=False))
        await r.ltrim(history_key, 0, settings.MAX_HISTORY_MESSAGES * 2 - 1)

    async def get_messages(self, user_id: int) -> List[Dict[str, str]]:
        r = await self.get_redis()
        history_key = self._get_history_key(user_id)

        messages = await r.lrange(history_key, 0, -1)

        result = []
        for msg in reversed(messages):
            try:
                result.append(json.loads(msg))
            except json.JSONDecodeError:
                continue

        return result

    async def clear_history(self, user_id: int):
        r = await self.get_redis()
        history_key = self._get_history_key(user_id)
        await r.delete(history_key)

    async def should_reset_history(self, message_content: str) -> bool:
        reset_keywords = [
            "сброс", "сброс истории", "забудь", "забыть", "очисти историю",
            "reset", "clear history", "forget", "новая тема", "новый разговор"
        ]

        message_lower = message_content.lower().strip()

        for keyword in reset_keywords:
            if keyword in message_lower:
                return True

        return False

    def should_reset_from_response(self, response_content: str) -> bool:
        """Определить необходимость сброса на основе ответа GPT"""
        reset_markers = [
            "RESET_HISTORY", "CLEAR_CONTEXT", "NEW_CONVERSATION"
        ]

        for marker in reset_markers:
            if marker in response_content:
                return True

        return False


history_manager = MessageHistory()