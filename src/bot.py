import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum

from redis.exceptions import RedisError
from telegram import (
    BotCommand,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .ai import analyze_image, audio_to_text, create_chat_response, create_image, edit_image
from .history import history_manager
from .metrics import anonymous_id, record_event
from .rate_limit import rate_limiter
from .redis_client import close_redis, get_redis
from .settings import settings

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TELEGRAM_TEXT_LIMIT = 4_000
LOCATION_TTL = 600
DRAW_PATTERN = re.compile(
    r"^(?:пожалуйста[\s,]+)?"
    r"(?:нарисуй(?:те)?|рисуй(?:те)?|сгенерируй(?:те)?\s+(?:картинку|изображение))\b"
    r"[\s,:—-]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
EDIT_PATTERN = re.compile(
    r"^(?:/edit(?:@\w+)?|измени(?:те)?|отредактируй(?:те)?)\b[\s,:—-]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


class Intent(StrEnum):
    CHAT = "chat"
    DRAW = "draw"
    RESET = "reset"


@dataclass(frozen=True)
class ParsedIntent:
    intent: Intent
    prompt: str


@dataclass(frozen=True)
class Reply:
    data: str | bytes
    kind: str
    actions: bool = False

    async def send(self, message: Message) -> None:
        if self.kind == "image":
            await message.reply_photo(photo=self.data)
            return
        await send_text(
            message,
            str(self.data),
            reply_markup=answer_keyboard() if self.actions else None,
        )


def parse_intent(text: str) -> ParsedIntent:
    normalized = text.strip()
    if history_manager.should_reset_history(normalized):
        return ParsedIntent(Intent.RESET, "")
    draw_match = DRAW_PATTERN.match(normalized)
    if draw_match:
        return ParsedIntent(Intent.DRAW, draw_match.group(1).strip())
    return ParsedIntent(Intent.CHAT, normalized)


def conversation_id(update: Update) -> str:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if chat is None or user is None:
        raise ValueError("У обновления нет чата или пользователя")
    thread_id = getattr(message, "message_thread_id", None) or 0
    return f"{chat.id}:{user.id}:{thread_id}"


def split_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        split_at = max(rest.rfind("\n", 0, limit + 1), rest.rfind(" ", 0, limit + 1))
        if split_at < limit // 2:
            split_at = limit
        chunks.append(rest[:split_at].rstrip())
        rest = rest[split_at:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def answer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Короче", callback_data="answer:shorter"),
                InlineKeyboardButton("Подробнее", callback_data="answer:details"),
            ],
            [
                InlineKeyboardButton("Продолжить", callback_data="answer:continue"),
                InlineKeyboardButton("Нарисовать", callback_data="answer:draw"),
            ],
        ]
    )


def location_radius_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1 км", callback_data="location-radius:1"),
                InlineKeyboardButton("3 км", callback_data="location-radius:3"),
                InlineKeyboardButton("10 км", callback_data="location-radius:10"),
            ]
        ]
    )


def location_category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏛 Что посмотреть", callback_data="location:sights")],
            [InlineKeyboardButton("☕ Где поесть", callback_data="location:food")],
            [InlineKeyboardButton("🚶 Маршрут для прогулки", callback_data="location:walk")],
        ]
    )


async def send_text(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    chunks = split_text(text)
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        await message.reply_text(chunk, reply_markup=markup, disable_web_page_preview=True)


def _format_wait(seconds: int) -> str:
    if seconds >= 3_600:
        hours = max(1, (seconds + 3_599) // 3_600)
        return f"{hours} ч."
    minutes = max(1, (seconds + 59) // 60)
    return f"{minutes} мин."


async def check_limit(update: Update, cost: int) -> bool:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return False
    result = await rate_limiter.consume(user.id, cost)
    if result.allowed:
        record_event(
            "rate_limit",
            user=anonymous_id(user.id),
            allowed=True,
            cost=cost,
            remaining=result.remaining,
        )
        return True
    await message.reply_text(
        f"Лимит запросов исчерпан. Попробуй снова примерно через {_format_wait(result.retry_after)}"
    )
    record_event(
        "rate_limit",
        user=anonymous_id(user.id),
        allowed=False,
        cost=cost,
        retry_after=result.retry_after,
    )
    return False


@asynccontextmanager
async def chat_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
) -> AsyncIterator[None]:
    chat = update.effective_chat
    message = update.effective_message
    if chat is None:
        yield
        return

    async def keep_sending() -> None:
        while True:
            await context.bot.send_chat_action(
                chat_id=chat.id,
                action=action,
                message_thread_id=getattr(message, "message_thread_id", None),
            )
            await asyncio.sleep(4)

    task = asyncio.create_task(keep_sending())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


def _message_text(message: Message) -> str:
    return message.text or message.caption or ""


def _strip_bot_mention(text: str, username: str | None) -> str:
    if not username:
        return text
    return re.sub(rf"@{re.escape(username)}\b", "", text, flags=re.IGNORECASE).strip()


def should_respond_in_chat(
    update: Update,
    bot_id: int,
    bot_username: str | None,
) -> bool:
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return False
    if chat.type == Chat.PRIVATE:
        return True
    if settings.group_mode == "all":
        return True
    if settings.group_mode == "off":
        return False
    explicitly_addressed = bool(message.caption and EDIT_PATTERN.match(message.caption.strip()))
    replied_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot_id
    )
    mentioned = bool(bot_username and f"@{bot_username.lower()}" in _message_text(message).lower())
    return replied_to_bot or mentioned or explicitly_addressed


async def process_intent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parsed: ParsedIntent,
) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    current_conversation = conversation_id(update)

    if parsed.intent == Intent.RESET:
        await history_manager.clear_history(current_conversation)
        await message.reply_text("История этого диалога очищена.")
        return

    if parsed.intent == Intent.DRAW:
        if not parsed.prompt:
            await message.reply_text(
                "Добавь описание: /draw космический кот в стиле ретрофутуризма"
            )
            return
        if not await check_limit(update, settings.image_request_cost):
            return
        async with chat_action(update, context, ChatAction.UPLOAD_PHOTO):
            image = await create_image(parsed.prompt, user.id)
        await Reply(image, "image").send(message)
        return

    if not parsed.prompt:
        return
    if not await check_limit(update, settings.text_request_cost):
        return
    async with chat_action(update, context, ChatAction.TYPING):
        answer = await create_chat_response(parsed.prompt, current_conversation, user.id)
    await Reply(answer, "text", actions=True).send(message)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    await message.reply_html(
        rf"Привет, {user.mention_html()}! Я Гопобот. Могу отвечать на вопросы, "
        "понимать голосовые и фотографии, искать места рядом и рисовать.\n\n"
        "Попробуй: «Объясни квантовую запутанность простыми словами» или "
        "«Нарисуй кота-космонавта».\n\n"
        "История хранится отдельно для этого диалога и удаляется после периода бездействия. "
        "Подробнее: /privacy"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        "Команды:\n"
        "/draw описание — нарисовать изображение\n"
        "/reset или /new — начать новый диалог\n"
        "/memory — состояние памяти\n"
        "/privacy — как хранятся данные\n"
        "/help — эта справка\n\n"
        "Пришли голосовое или фотографию. Чтобы изменить фото, добавь подпись "
        "«измени: …» или «/edit …». В группе я отвечаю на упоминание или reply."
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await history_manager.clear_history(conversation_id(update))
    await message.reply_text("История этого диалога очищена.")


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    info = await history_manager.get_memory_info(conversation_id(update))
    summary = "есть" if info.has_summary else "ещё нет"
    ttl = _format_wait(info.ttl_seconds) if info.ttl_seconds else "данных пока нет"
    await message.reply_text(
        f"В памяти этого диалога: {info.messages} сообщений. Краткое резюме: {summary}. "
        f"До автоматического удаления при отсутствии новых сообщений: {ttl}.\n"
        "Очистить сейчас: /reset"
    )


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    days = max(1, settings.history_ttl // 86_400)
    await message.reply_text(
        "Бот отправляет OpenAI текст, голосовые, фотографии и выбранную геолокацию, "
        "когда это нужно для ответа. Контекст хранится в Redis отдельно для каждого "
        f"чата, пользователя и темы до {days} дней после последней активности. "
        "Координаты геолокации в контекст не записываются и удаляются через 10 минут. "
        "Старые сообщения сжимаются в краткое резюме. /reset удаляет контекст сразу. "
        "Полный текст сообщений в служебные логи по умолчанию не записывается."
    )


async def draw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = " ".join(context.args).strip()
    await process_intent(update, context, ParsedIntent(Intent.DRAW, prompt))


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if not should_respond_in_chat(update, context.bot.id, context.bot.username):
        return
    text = _strip_bot_mention(message.text or "", context.bot.username)
    fields = {
        "user": anonymous_id(user.id),
        "chat": anonymous_id(update.effective_chat.id) if update.effective_chat else None,
        "message_type": "text",
        "characters": len(text),
    }
    if settings.log_user_content:
        fields["content"] = text
    record_event("incoming_message", **fields)
    await process_intent(update, context, parse_intent(text))


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not message.photo:
        return
    if not should_respond_in_chat(update, context.bot.id, context.bot.username):
        return

    caption = _strip_bot_mention(message.caption or "", context.bot.username)
    edit_match = EDIT_PATTERN.match(caption)
    is_edit = bool(edit_match)
    prompt = edit_match.group(1).strip() if edit_match else caption
    if is_edit and not prompt:
        await message.reply_text("В подписи к фото укажи, что изменить: «измени: добавь радугу».")
        return
    if not prompt:
        prompt = "Опиши, что изображено на фото."
    cost = settings.image_request_cost if is_edit else settings.photo_request_cost
    if not await check_limit(update, cost):
        return

    photo_file = await message.photo[-1].get_file()
    image_bytes = bytes(await photo_file.download_as_bytearray())
    if is_edit:
        async with chat_action(update, context, ChatAction.UPLOAD_PHOTO):
            edited = await edit_image(image_bytes, prompt, user.id)
        await Reply(edited, "image").send(message)
        return

    async with chat_action(update, context, ChatAction.TYPING):
        answer = await analyze_image(
            image_bytes,
            prompt,
            conversation_id(update),
            user.id,
        )
    await Reply(answer, "text", actions=True).send(message)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or message.voice is None:
        return
    if not should_respond_in_chat(update, context.bot.id, context.bot.username):
        return
    if not await check_limit(update, settings.voice_request_cost):
        return

    voice_file = await message.voice.get_file()
    voice_bytes = bytes(await voice_file.download_as_bytearray())
    async with chat_action(update, context, ChatAction.TYPING):
        text = await audio_to_text(voice_bytes, user.id)
    record_event(
        "voice_transcribed",
        user=anonymous_id(user.id),
        characters=len(text),
        content=text if settings.log_user_content else None,
    )
    parsed = parse_intent(text)
    if parsed.intent == Intent.DRAW and not await check_limit(update, settings.image_request_cost):
        await message.reply_text(f"Распознано: «{text}»")
        return
    if parsed.intent == Intent.DRAW:
        async with chat_action(update, context, ChatAction.UPLOAD_PHOTO):
            image = await create_image(parsed.prompt, user.id)
        await Reply(image, "image").send(message)
        return
    if parsed.intent == Intent.RESET:
        await history_manager.clear_history(conversation_id(update))
        await message.reply_text("История этого диалога очищена.")
        return
    async with chat_action(update, context, ChatAction.TYPING):
        answer = await create_chat_response(text, conversation_id(update), user.id)
    await Reply(answer, "text", actions=True).send(message)


async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or message.location is None:
        return
    if not should_respond_in_chat(update, context.bot.id, context.bot.username):
        return
    state = {
        "latitude": message.location.latitude,
        "longitude": message.location.longitude,
    }
    await get_redis().setex(
        f"location:{conversation_id(update)}",
        LOCATION_TTL,
        json.dumps(state),
    )
    await message.reply_text(
        "В каком радиусе искать? Координаты для этого выбора удалятся через 10 минут.",
        reply_markup=location_radius_keyboard(),
    )


async def location_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    message = update.effective_message
    user = update.effective_user
    if query is None or message is None or user is None:
        return
    await query.answer()
    state_key = f"location:{conversation_id(update)}"
    raw_state = await get_redis().get(state_key)
    if not raw_state:
        await message.reply_text("Геолокация устарела. Пришли её ещё раз.")
        return
    state = json.loads(raw_state)
    if query.data.startswith("location-radius:"):
        radius = int(query.data.split(":", 1)[1])
        state["radius"] = radius
        await get_redis().setex(state_key, LOCATION_TTL, json.dumps(state))
        await message.reply_text(
            f"Ищу в радиусе {radius} км. Что тебя интересует?",
            reply_markup=location_category_keyboard(),
        )
        return
    if not await check_limit(update, settings.text_request_cost):
        return
    category = query.data.split(":", 1)[1]
    choices = {
        "sights": "достопримечательности и интересные места",
        "food": "хорошие кафе и рестораны",
        "walk": "приятный пеший маршрут с несколькими остановками",
    }
    subject = choices.get(category, choices["sights"])
    radius = int(state.get("radius", settings.location_radius_km))
    latitude = state["latitude"]
    longitude = state["longitude"]
    prompt = (
        f"Найди {subject} в радиусе примерно {radius} км от координат "
        f"{latitude}, {longitude}. Дай короткий список с адресами и объясни выбор. "
        "Не выдумывай места; если актуальность проверить нельзя, прямо скажи об этом."
    )
    # Координаты не попадают в историю диалога: там остаётся только суть запроса.
    history_prompt = f"[Пользователь отправил геолокацию] Найди {subject} в радиусе {radius} км."
    async with chat_action(update, context, ChatAction.TYPING):
        answer = await create_chat_response(
            prompt,
            conversation_id(update),
            user.id,
            use_web_search=settings.location_web_search,
            history_prompt=history_prompt,
        )
    map_url = f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"
    await Reply(f"{answer}\n\nТочка на карте: {map_url}", "text", actions=True).send(message)


async def answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    message = update.effective_message
    user = update.effective_user
    if query is None or message is None or user is None:
        return
    await query.answer()
    action = query.data.split(":", 1)[1]
    current_conversation = conversation_id(update)
    if action == "draw":
        if not await check_limit(update, settings.image_request_cost):
            return
        source = await history_manager.get_last_assistant_message(current_conversation)
        if not source:
            await message.reply_text("Сначала дождись текстового ответа, который нужно изобразить.")
            return
        async with chat_action(update, context, ChatAction.UPLOAD_PHOTO):
            image = await create_image(f"Создай иллюстрацию к этому тексту:\n{source}", user.id)
        await Reply(image, "image").send(message)
        return

    prompts = {
        "shorter": "Перепиши свой предыдущий ответ заметно короче, сохранив главное.",
        "details": "Раскрой свой предыдущий ответ подробнее и добавь полезные примеры.",
        "continue": "Продолжи предыдущий ответ с того места, где остановился.",
    }
    prompt = prompts.get(action)
    if prompt is None:
        return
    if not await check_limit(update, settings.text_request_cost):
        return
    async with chat_action(update, context, ChatAction.TYPING):
        answer = await create_chat_response(prompt, current_conversation, user.id)
    await Reply(answer, "text", actions=True).send(message)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Необработанная ошибка при обработке Telegram update", exc_info=context.error)
    record_event(
        "handler_error",
        error_type=type(context.error).__name__ if context.error else "Unknown",
    )
    if isinstance(update, Update) and update.effective_message:
        with suppress(Exception):
            await update.effective_message.reply_text(
                "Что-то пошло не так. Я уже записал ошибку — попробуй ещё раз немного позже."
            )


async def post_init(application: Application) -> None:
    for attempt in range(1, 13):
        try:
            await get_redis().ping()
            break
        except RedisError:
            if attempt == 12:
                raise
            record_event("redis_startup_wait", attempt=attempt)
            await asyncio.sleep(2)
    legacy_keys_updated = await history_manager.expire_legacy_history()
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Начать работу"),
            BotCommand("draw", "Нарисовать изображение"),
            BotCommand("new", "Начать новый диалог"),
            BotCommand("memory", "Показать состояние памяти"),
            BotCommand("privacy", "Как хранятся данные"),
            BotCommand("help", "Показать справку"),
        ]
    )
    record_event(
        "bot_started",
        chat_model=settings.chat_model,
        image_model=settings.image_model,
        legacy_keys_updated=legacy_keys_updated,
    )


async def post_shutdown(application: Application) -> None:
    await close_redis()
    from .ai import client

    await client.close()
    record_event("bot_stopped")


def build_application() -> Application:
    application = (
        Application.builder()
        .token(settings.token)
        .concurrent_updates(False)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler(["reset", "new"], reset_command))
    application.add_handler(CommandHandler("memory", memory_command))
    application.add_handler(CommandHandler("privacy", privacy_command))
    application.add_handler(CommandHandler("draw", draw_command))
    application.add_handler(
        CallbackQueryHandler(location_callback, pattern=r"^location(?:-radius)?:")
    )
    application.add_handler(CallbackQueryHandler(answer_callback, pattern=r"^answer:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(MessageHandler(filters.LOCATION, location_handler))
    application.add_handler(MessageHandler(filters.VOICE, voice_handler))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
