import os
import tempfile

from openai import AsyncOpenAI
from pydub import AudioSegment

from history import history_manager
import settings

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def create_chat_response(prompt: str, user_id: int):
    await history_manager.add_message(user_id, "user", prompt)

    history = await history_manager.get_messages(user_id)

    if not history:
        history = [{"role": "user", "content": prompt}]

    response = await client.chat.completions.create(
        model="gpt-4.1-nano-2025-04-14",
        messages=history,
        temperature=0.2,
        max_tokens=32768,
        top_p=1.0,
        frequency_penalty=0.2,
        presence_penalty=0.0,
    )

    if response and response.choices and len(response.choices) > 0:
        assistant_message = response.choices[0].message.content.strip()
        await history_manager.add_message(user_id, "assistant", assistant_message)
        return assistant_message


async def create_image(prompt):
    response = await client.images.generate(prompt=prompt, n=1, size="1024x1024")
    if response and response.data and len(response.data) > 0:
        return response.data[0].url


async def edit_image(img_bytes, prompt):
    r = await client.images.edit(
        image=img_bytes,
        # mask=open("mask.png", "rb"),
        prompt=prompt,
        n=1,
        size="1024x1024",
    )
    if r and r.data and len(r.data) > 0:
        return r.data[0].url


async def determine_image(img_bytes):
    response = await client.chat.completions.create(
        model="gpt-4.1-nano-2025-04-14",
        messages=[
            {"role": "assistant", "content": "Что изображено на картинке: %s" % img_bytes},
        ],
        temperature=0.2,
        max_tokens=1000,
        top_p=1.0,
        frequency_penalty=0.2,
        presence_penalty=0.0,
    )
    if response and response.choices and len(response.choices) > 0:
        return response.choices[0].message.content.strip()


async def audio_to_text(audio_file):
    output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")

    try:
        ogg_audio = AudioSegment.from_ogg(audio_file)
        ogg_audio.export(output_file, format="mp3")
        output_file.close()  # закрываем файл, чтоб его увидел ffmpeg

        # шлем в openAI
        with open(output_file.name, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        return transcript.text if transcript else None
    finally:
        os.remove(output_file.name)
