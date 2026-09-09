from types import SimpleNamespace

from src import bot
from src.bot import EDIT_PATTERN, Intent, parse_intent, should_respond_in_chat, split_text


def test_reset_requires_an_exact_phrase() -> None:
    assert parse_intent("не забудь купить молоко").intent == Intent.CHAT
    assert parse_intent("Как забыть пароль?").intent == Intent.CHAT
    assert parse_intent("  новый   разговор ").intent == Intent.RESET


def test_draw_intent_extracts_only_the_description() -> None:
    parsed = parse_intent("Пожалуйста, нарисуй: кота в космосе")

    assert parsed.intent == Intent.DRAW
    assert parsed.prompt == "кота в космосе"


def test_long_text_is_split_within_telegram_limit() -> None:
    chunks = split_text(("слово " * 2_000).strip(), limit=100)

    assert "".join(chunks).replace(" ", "") == ("слово" * 2_000)
    assert all(0 < len(chunk) <= 100 for chunk in chunks)


def _group_update(
    text: str = "",
    *,
    caption: str | None = None,
    reply_user_id: int | None = None,
) -> SimpleNamespace:
    reply = None
    if reply_user_id is not None:
        reply = SimpleNamespace(from_user=SimpleNamespace(id=reply_user_id))
    message = SimpleNamespace(text=text, caption=caption, reply_to_message=reply)
    return SimpleNamespace(
        effective_chat=SimpleNamespace(type="supergroup"),
        effective_message=message,
    )


def test_group_mode_reacts_to_mentions_and_replies(monkeypatch) -> None:
    monkeypatch.setattr(bot, "settings", SimpleNamespace(group_mode="mentions"))

    assert should_respond_in_chat(_group_update("@gopobot привет"), 42, "gopobot")
    assert should_respond_in_chat(_group_update(reply_user_id=42), 42, "gopobot")
    assert should_respond_in_chat(
        _group_update(caption="/edit добавь радугу"),
        42,
        "gopobot",
    )
    assert not should_respond_in_chat(_group_update("обычное сообщение"), 42, "gopobot")


def test_draw_and_edit_keywords_must_be_whole_words() -> None:
    assert parse_intent("Нарисуйте кота").prompt == "кота"
    assert parse_intent("рисую я плохо").intent == Intent.CHAT
    assert parse_intent("Нарисованный кот лучше живого?").intent == Intent.CHAT

    assert EDIT_PATTERN.match("Отредактируйте фон").group(1) == "фон"
    assert EDIT_PATTERN.match("/edit@gopobot добавь радугу").group(1) == "добавь радугу"
    assert EDIT_PATTERN.match("Изменилось ли что-то на фото?") is None
    assert EDIT_PATTERN.match("Изменить фон можно?") is None
