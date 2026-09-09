from types import SimpleNamespace
from unittest.mock import AsyncMock

from src import ai


async def test_chat_uses_responses_without_remote_storage(monkeypatch) -> None:
    fake_response = SimpleNamespace(
        output_text="Готовый ответ",
        usage=SimpleNamespace(input_tokens=5, output_tokens=3, total_tokens=8),
    )
    create = AsyncMock(return_value=fake_response)
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=create))
    fake_history = SimpleNamespace(
        add_message=AsyncMock(),
        get_messages=AsyncMock(return_value=[{"role": "user", "content": "Привет"}]),
        get_summary=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(ai, "client", fake_client)
    monkeypatch.setattr(ai, "history_manager", fake_history)

    result = await ai.create_chat_response("Привет", "1:1:0", 1)

    assert result == "Готовый ответ"
    request = create.await_args.kwargs
    assert request["store"] is False
    assert request["model"] == ai.settings.chat_model
    assert request["max_output_tokens"] == ai.settings.max_output_tokens
    assert request["reasoning"] == {"effort": "none"}
    assert request["text"] == {"verbosity": "low"}
    assert fake_history.add_message.await_count == 2


async def test_vision_sends_an_image_content_block(monkeypatch) -> None:
    fake_response = SimpleNamespace(
        output_text="На фото кот",
        usage=SimpleNamespace(input_tokens=5, output_tokens=3, total_tokens=8),
    )
    create = AsyncMock(return_value=fake_response)
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=create))
    fake_history = SimpleNamespace(
        add_message=AsyncMock(),
        get_messages=AsyncMock(
            return_value=[{"role": "user", "content": "[Пользователь отправил фото] Что здесь?"}]
        ),
        get_summary=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(ai, "client", fake_client)
    monkeypatch.setattr(ai, "history_manager", fake_history)

    result = await ai.analyze_image(b"jpeg", "Что здесь?", "1:1:0", 1)

    assert result == "На фото кот"
    content = create.await_args.kwargs["input"][-1]["content"]
    assert content[0] == {"type": "input_text", "text": "Что здесь?"}
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,")


async def test_history_prompt_is_stored_while_model_gets_full_text(monkeypatch) -> None:
    fake_response = SimpleNamespace(output_text="Кафе рядом", usage=None)
    create = AsyncMock(return_value=fake_response)
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=create))
    stored = "[Пользователь отправил геолокацию] Найди кафе в радиусе 3 км."
    fake_history = SimpleNamespace(
        add_message=AsyncMock(),
        get_messages=AsyncMock(return_value=[{"role": "user", "content": stored}]),
        get_summary=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(ai, "client", fake_client)
    monkeypatch.setattr(ai, "history_manager", fake_history)

    full = "Найди кафе в радиусе 3 км от координат 55.75, 37.61."
    await ai.create_chat_response(full, "1:1:0", 1, history_prompt=stored)

    assert fake_history.add_message.await_args_list[0].args == ("1:1:0", "user", stored)
    assert create.await_args.kwargs["input"] == [{"role": "user", "content": full}]
