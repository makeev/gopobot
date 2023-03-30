import io

import openai
from PIL import Image


def crop_to_square(image_bytes):
    # Открыть изображение из байтов
    image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    # Получить размеры изображения
    width, height = image.size

    # Выбрать минимальный размер
    crop_size = min(width, height)

    # Вычислить координаты прямоугольника для обрезки
    left = (width - crop_size) // 2
    top = (height - crop_size) // 2
    right = (width + crop_size) // 2
    bottom = (height + crop_size) // 2

    # Обрезать изображение
    cropped_image = image.crop((left, top, right, bottom))

    # Сохранить обрезанное изображение в буфер BytesIO
    buffer = io.BytesIO()
    cropped_image.save(buffer, format="PNG")
    buffer.seek(0)

    # Вернуть содержимое буфера в виде байтов
    return buffer.getvalue()


def resize_image_bytes(img_bytes, new_size, save_aspect_ratio=True):
    # Load the image from bytes
    img = Image.open(io.BytesIO(img_bytes))

    # Calculate the aspect ratio of the original image
    orig_width, orig_height = img.size
    orig_aspect_ratio = orig_width / orig_height

    if save_aspect_ratio:
        # Calculate the aspect ratio of the new size
        new_width, new_height = new_size
        new_aspect_ratio = new_width / new_height

        # Determine whether to adjust the width or height to match the aspect ratio
        if new_aspect_ratio > orig_aspect_ratio:
            # Adjust the width to match the aspect ratio
            new_width = int(new_height * orig_aspect_ratio)
        else:
            # Adjust the height to match the aspect ratio
            new_height = int(new_width / orig_aspect_ratio)

        # Resize the image with the adjusted size
        resized_img = img.resize((new_width, new_height))
    else:
        # Resize the image without preserving aspect ratio
        resized_img = img.resize(new_size)

    # Convert the resized image to bytes
    with io.BytesIO() as output:
        resized_img.save(output, format="JPEG")
        resized_bytes = output.getvalue()

    return resized_bytes
