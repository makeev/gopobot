import logging
import os
import tempfile
from dataclasses import dataclass

import openai
import redis.asyncio as redis
from telegram import __version__ as TG_VER

import settings
from ai import audio_to_text, create_chat_response, create_image, determine_image
from images import resize_image_bytes

openai.api_key = settings.OPENAI_API_KEY

try:
    from telegram import __version_info__
except ImportError:
    __version_info__ = (0, 0, 0, 0, 0)  # type: ignore[assignment]

if __version_info__ < (20, 0, 0, "alpha", 1):
    raise RuntimeError(
        f"This example is not compatible with your current PTB version {TG_VER}. To view the "
        f"{TG_VER} version of this example, "
        f"visit https://docs.python-telegram-bot.org/en/v{TG_VER}/examples.html"
    )
from telegram import ForceReply, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


async def get_redis():
    return await redis.from_url(settings.REDIS_URL)


def user_moderation():
    """Декоратор для модерации пользователей"""

    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            r = await get_redis()
            key = f"counter:{update.effective_user.id}"
            counter = await r.get(key)
            if counter is None:
                # создаем счетчик
                await r.setex(key, 60 * 60 * 24, 0)
            elif int(counter) > settings.LIMIT_MESSAGES and update.effective_user.id not in settings.ADMIN_USERS:
                await update.message.reply_text("Вы превысили лимит сообщений")
                return

            if update.effective_user.id not in settings.ADMIN_USERS:
                await update.message.reply_text("Вы не в списке разрешенных пользователей")
                return

            await r.incr(key)
            return await func(update, context)

        return wrapper

    return decorator


# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        rf"Привет, {user.mention_html()}! Спроси меня что-то или попроси нарисовать",
        reply_markup=ForceReply(selective=True),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Спроси меня что-то или попроси нарисовать")


@dataclass
class Reply:
    data: str
    type: str

    async def send_reply(self, update: Update):
        if self.type == "text":
            await update.message.reply_text(self.data)
        elif self.type == "image":
            await update.message.reply_photo(photo=self.data)


async def _proceed_message(text: str):
    try:
        if "нарису" in text.lower():
            img_url = await create_image(text)
            if not img_url:
                return Reply(data="Не получилось нарисовать", type="text")
            else:
                return Reply(data=img_url, type="image")
        else:
            answer = await create_chat_response(text)
            if not answer:
                return Reply(data="Гопоту заклинило, попробуйте позже", type="text")
            else:
                return Reply(data=answer, type="text")
    except Exception as e:
        return Reply(data="Что-то пошло не так, попробуйте позже: %s" % str(e), type="text")


@user_moderation()
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    reply = await _proceed_message(update.message.text)
    await reply.send_reply(update)


@user_moderation()
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    await update.message.reply_text("Я пока не умею анализировать фотографии")
    return
    photo_file = await update.message.photo[-1].get_file()
    img_bytes = await photo_file.download_as_bytearray()
    img_bytes = resize_image_bytes(img_bytes, (480, 480))
    reply = await determine_image(img_bytes)
    if reply:
        await update.message.reply_text(reply)
    else:
        await update.message.reply_text("Не получилось определить изображение")
    # await update.message.reply_photo(photo=img_bytes)


@user_moderation()
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    location = update.message.location
    reply = await create_chat_response(
        f"Где находятся координаты и список достопримечательностей рядом: {location.latitude}, {location.longitude}"
    )

    if not reply:
        await update.message.reply_text("Гопоту заклинило, попробуйте позже")
    else:
        await update.message.reply_text(reply)


@user_moderation()
async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Аудио сообщение"""
    # audio to text
    audio_file = await update.message.voice.get_file()

    tmp_file = tempfile.NamedTemporaryFile(delete=False)

    try:
        bytes_array = await audio_file.download_as_bytearray()
        tmp_file.write(bytes_array)
        tmp_file.close()  # закрываем файл, чтоб его увидел ffmpeg

        text = await audio_to_text(open(tmp_file.name, "rb"))
        print(text)
        reply = await _proceed_message(text)
        await reply.send_reply(update)
    finally:
        # delete tmp_file
        os.remove(tmp_file.name)


def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(settings.TOKEN).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(MessageHandler(filters.LOCATION, location_handler))
    application.add_handler(MessageHandler(filters.VOICE, voice_handler))

    # Run the bot until the user presses Ctrl-C
    application.run_polling()


if __name__ == "__main__":
    main()
