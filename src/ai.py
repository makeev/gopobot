import os
import tempfile
from pathlib import Path

import openai
from pydub import AudioSegment


async def create_chat_response(prompt):
    response = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "assistant", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=4000,
        top_p=1.0,
        frequency_penalty=0.2,
        presence_penalty=0.0,
    )
    if response and hasattr(response, "choices") and len(response.choices) > 0:
        return response.choices[0].message.content.strip()


async def create_image(prompt):
    response = await openai.Image.acreate(prompt=prompt, n=1, size="1024x1024")
    if response and hasattr(response, "data") and len(response.data) > 0:
        return response.data[0].url


async def edit_image(img_bytes, prompt):
    r = await openai.Image.acreate_edit(
        image=img_bytes,
        # mask=open("mask.png", "rb"),
        prompt=prompt,
        n=1,
        size="1024x1024",
    )
    if r and len(r.data) > 0:
        return r.data[0].url


async def determine_image(img_bytes):
    response = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "assistant", "content": "Что изображено на картинке: %s" % img_bytes},
        ],
        temperature=0.2,
        max_tokens=100,
        top_p=1.0,
        frequency_penalty=0.2,
        presence_penalty=0.0,
    )
    if response and hasattr(response, "choices") and len(response.choices) > 0:
        return response.choices[0].message.content.strip()


async def audio_to_text(audio_file):
    output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")

    try:
        ogg_audio = AudioSegment.from_ogg(audio_file)
        ogg_audio.export(output_file, format="mp3")
        output_file.close()  # закрываем файл, чтоб его увидел ffmpeg

        # шлем в openAI
        transcript = await openai.Audio.atranscribe("whisper-1", open(output_file.name, "rb"))
        return transcript.text if transcript else None
    finally:
        os.remove(output_file.name)
