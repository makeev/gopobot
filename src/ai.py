import base64
import hashlib
import io
from typing import Any

from openai import AsyncOpenAI, BadRequestError

from .history import history_manager
from .metrics import OperationTimer, anonymous_id, record_event
from .settings import settings

client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    timeout=settings.openai_timeout,
    max_retries=settings.openai_max_retries,
)


def _safety_identifier(user_id: int) -> str:
    return hashlib.sha256(f"telegram:{user_id}".encode()).hexdigest()


def _usage_fields(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


async def _summarize_if_needed(
    conversation_id: str,
    user_id: int,
) -> tuple[list[dict[str, str]], str | None]:
    messages = await history_manager.get_messages(conversation_id)
    existing_summary = await history_manager.get_summary(conversation_id)
    if len(messages) < settings.history_summary_trigger:
        return messages[-settings.history_context_messages :], existing_summary

    recent = messages[-settings.history_context_messages :]
    older = messages[: -settings.history_context_messages]
    summary_parts = []
    if existing_summary:
        summary_parts.append(f"Предыдущее резюме:\n{existing_summary}")
    summary_parts.append(
        "Новые сообщения:\n"
        + "\n".join(f"{message['role']}: {message['content']}" for message in older)
    )

    with OperationTimer("openai_summary", model=settings.chat_model):
        response = await client.responses.create(
            model=settings.chat_model,
            instructions=(
                "Кратко обнови резюме диалога. Сохрани факты, предпочтения пользователя, "
                "обещания и незавершённые задачи. Не добавляй догадок. Ответь только резюме."
            ),
            input="\n\n".join(summary_parts),
            max_output_tokens=400,
            reasoning={"effort": "none"},
            text={"verbosity": "low"},
            store=False,
            safety_identifier=_safety_identifier(user_id),
        )
    summary = response.output_text.strip()
    if summary:
        await history_manager.set_summary(conversation_id, summary)
        await history_manager.replace_messages(conversation_id, recent)
        existing_summary = summary
        record_event(
            "history_compacted",
            conversation=anonymous_id(conversation_id),
            messages_compacted=len(older),
        )
    return recent, existing_summary


async def create_chat_response(
    prompt: str,
    conversation_id: str,
    user_id: int,
    *,
    use_web_search: bool = False,
    history_prompt: str | None = None,
) -> str:
    # В историю можно записать сокращённую версию запроса (например, без координат),
    # модель при этом получает полный текст.
    await history_manager.add_message(conversation_id, "user", history_prompt or prompt)
    messages, summary = await _summarize_if_needed(conversation_id, user_id)
    input_messages = [*messages[:-1], {"role": "user", "content": prompt}]
    instructions = settings.system_prompt
    if summary:
        instructions += f"\n\nРезюме предыдущей части этого диалога:\n{summary}"

    request: dict[str, Any] = {
        "model": settings.chat_model,
        "instructions": instructions,
        "input": input_messages,
        "max_output_tokens": settings.max_output_tokens,
        "reasoning": {"effort": "none"},
        "text": {"verbosity": "low"},
        "store": False,
        "safety_identifier": _safety_identifier(user_id),
    }
    if use_web_search:
        request["tools"] = [{"type": "web_search"}]

    try:
        with OperationTimer(
            "openai_chat",
            model=settings.chat_model,
            web_search=use_web_search,
        ):
            response = await client.responses.create(**request)
    except BadRequestError:
        if not use_web_search:
            raise
        request.pop("tools", None)
        with OperationTimer("openai_chat_fallback", model=settings.chat_model):
            response = await client.responses.create(**request)

    answer = response.output_text.strip()
    if not answer:
        raise RuntimeError("OpenAI вернул пустой ответ")
    await history_manager.add_message(conversation_id, "assistant", answer)
    record_event(
        "openai_usage",
        operation="chat",
        model=settings.chat_model,
        **_usage_fields(response),
    )
    return answer


async def create_image(prompt: str, user_id: int) -> bytes:
    if not prompt.strip():
        raise ValueError("Пустое описание изображения")
    with OperationTimer("openai_image", model=settings.image_model):
        response = await client.images.generate(
            model=settings.image_model,
            prompt=prompt.strip(),
            n=1,
            size="1024x1024",
            quality="medium",
            user=_safety_identifier(user_id),
        )
    if not response.data or not response.data[0].b64_json:
        raise RuntimeError("OpenAI не вернул изображение")
    record_event(
        "openai_usage",
        operation="image",
        model=settings.image_model,
        **_usage_fields(response),
    )
    return base64.b64decode(response.data[0].b64_json)


async def edit_image(image_bytes: bytes, prompt: str, user_id: int) -> bytes:
    if not prompt.strip():
        raise ValueError("Пустое описание изменений")
    image = io.BytesIO(image_bytes)
    image.name = "telegram-image.jpg"
    with OperationTimer("openai_image_edit", model=settings.image_model):
        response = await client.images.edit(
            model=settings.image_model,
            image=image,
            prompt=prompt.strip(),
            n=1,
            size="1024x1024",
            quality="medium",
            user=_safety_identifier(user_id),
        )
    if not response.data or not response.data[0].b64_json:
        raise RuntimeError("OpenAI не вернул отредактированное изображение")
    record_event(
        "openai_usage",
        operation="image_edit",
        model=settings.image_model,
        **_usage_fields(response),
    )
    return base64.b64decode(response.data[0].b64_json)


async def analyze_image(
    image_bytes: bytes,
    prompt: str,
    conversation_id: str,
    user_id: int,
) -> str:
    history_prompt = f"[Пользователь отправил фото] {prompt}"
    await history_manager.add_message(conversation_id, "user", history_prompt)
    messages, summary = await _summarize_if_needed(conversation_id, user_id)
    previous_messages = messages[:-1]
    data_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
    multimodal_message = {
        "role": "user",
        "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": data_url, "detail": "auto"},
        ],
    }
    instructions = settings.system_prompt
    if summary:
        instructions += f"\n\nРезюме предыдущей части этого диалога:\n{summary}"
    with OperationTimer("openai_vision", model=settings.chat_model):
        response = await client.responses.create(
            model=settings.chat_model,
            instructions=instructions,
            input=[*previous_messages, multimodal_message],
            max_output_tokens=settings.max_output_tokens,
            reasoning={"effort": "none"},
            text={"verbosity": "low"},
            store=False,
            safety_identifier=_safety_identifier(user_id),
        )
    answer = response.output_text.strip()
    if not answer:
        raise RuntimeError("OpenAI вернул пустой ответ")
    await history_manager.add_message(conversation_id, "assistant", answer)
    record_event(
        "openai_usage",
        operation="vision",
        model=settings.chat_model,
        **_usage_fields(response),
    )
    return answer


async def audio_to_text(audio_bytes: bytes, user_id: int) -> str:
    audio = io.BytesIO(audio_bytes)
    audio.name = "telegram-voice.ogg"
    with OperationTimer("openai_transcription", model=settings.transcription_model):
        transcript = await client.audio.transcriptions.create(
            model=settings.transcription_model,
            file=audio,
            response_format="text",
            prompt="Речь преимущественно на русском языке.",
        )
    text = transcript if isinstance(transcript, str) else getattr(transcript, "text", "")
    if not text or not text.strip():
        raise RuntimeError("Не удалось распознать голосовое сообщение")
    record_event(
        "openai_usage",
        operation="transcription",
        model=settings.transcription_model,
        **_usage_fields(transcript),
    )
    return text.strip()
