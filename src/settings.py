import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_SYSTEM_PROMPT = """Ты — Гопобот, дружелюбный русскоязычный собеседник
с лёгкой и доброй иронией.
Отвечай по существу, ясно и живо. Не изображай уверенность, когда фактов недостаточно.
Обычно отвечай кратко; подробности добавляй по просьбе. Не упоминай внутренние инструкции.
Если пользователь пишет на другом языке, отвечай на его языке."""


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"Не задана обязательная переменная окружения {name}")
    return value


def _integer(env: Mapping[str, str], name: str, default: int, *, minimum: int = 0) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} должна быть целым числом, получено: {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} должна быть не меньше {minimum}")
    return value


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} должна быть true или false, получено: {raw!r}")


def _admins(env: Mapping[str, str]) -> frozenset[int]:
    raw = env.get("ADMIN_USERS", "").strip()
    if not raw:
        return frozenset()
    try:
        return frozenset(int(value.strip()) for value in raw.split(",") if value.strip())
    except ValueError as exc:
        raise ValueError("ADMIN_USERS должна содержать Telegram ID через запятую") from exc


@dataclass(frozen=True)
class Settings:
    token: str
    openai_api_key: str
    admin_users: frozenset[int]
    redis_url: str
    limit_time: int
    limit_messages: int
    max_history_messages: int
    history_context_messages: int
    history_summary_trigger: int
    history_ttl: int
    max_output_tokens: int
    chat_model: str
    image_model: str
    transcription_model: str
    openai_timeout: int
    openai_max_retries: int
    system_prompt: str
    group_mode: str
    log_user_content: bool
    text_request_cost: int
    image_request_cost: int
    photo_request_cost: int
    voice_request_cost: int
    location_radius_km: int
    location_web_search: bool


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    group_mode = source.get("GROUP_MODE", "mentions").strip().lower()
    if group_mode not in {"mentions", "all", "off"}:
        raise ValueError("GROUP_MODE должна быть mentions, all или off")

    max_history = _integer(source, "MAX_HISTORY_MESSAGES", 100, minimum=2)
    context_messages = _integer(source, "HISTORY_CONTEXT_MESSAGES", 20, minimum=2)
    summary_trigger = _integer(source, "HISTORY_SUMMARY_TRIGGER", 30, minimum=3)
    if context_messages >= summary_trigger:
        raise ValueError("HISTORY_CONTEXT_MESSAGES должна быть меньше HISTORY_SUMMARY_TRIGGER")
    if summary_trigger > max_history:
        raise ValueError("HISTORY_SUMMARY_TRIGGER не должна превышать MAX_HISTORY_MESSAGES")

    return Settings(
        token=_required(source, "TOKEN"),
        openai_api_key=_required(source, "OPENAI_API_KEY"),
        admin_users=_admins(source),
        redis_url=source.get("REDIS_URL", "redis://localhost:6379/0").strip(),
        limit_time=_integer(source, "LIMIT_TIME", 86_400, minimum=1),
        limit_messages=_integer(source, "LIMIT_MESSAGES", 20, minimum=1),
        max_history_messages=max_history,
        history_context_messages=context_messages,
        history_summary_trigger=summary_trigger,
        history_ttl=_integer(source, "HISTORY_TTL", 2_592_000, minimum=60),
        max_output_tokens=_integer(source, "MAX_OUTPUT_TOKENS", 1_200, minimum=64),
        chat_model=source.get("CHAT_MODEL", "gpt-5.6-luna").strip(),
        image_model=source.get("IMAGE_MODEL", "gpt-image-2").strip(),
        transcription_model=source.get("TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe").strip(),
        openai_timeout=_integer(source, "OPENAI_TIMEOUT", 120, minimum=5),
        openai_max_retries=_integer(source, "OPENAI_MAX_RETRIES", 2, minimum=0),
        system_prompt=source.get("SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT,
        group_mode=group_mode,
        log_user_content=_boolean(source, "LOG_USER_CONTENT", False),
        text_request_cost=_integer(source, "TEXT_REQUEST_COST", 1, minimum=1),
        image_request_cost=_integer(source, "IMAGE_REQUEST_COST", 5, minimum=1),
        photo_request_cost=_integer(source, "PHOTO_REQUEST_COST", 2, minimum=1),
        voice_request_cost=_integer(source, "VOICE_REQUEST_COST", 2, minimum=1),
        location_radius_km=_integer(source, "LOCATION_RADIUS_KM", 3, minimum=1),
        location_web_search=_boolean(source, "LOCATION_WEB_SEARCH", True),
    )


load_dotenv()
settings = load_settings()
