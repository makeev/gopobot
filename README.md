# Gopobot

Русскоязычный Telegram-бот на OpenAI API. Он ведёт диалог с контекстом, понимает
голосовые и фотографии, создаёт и редактирует изображения и помогает искать места
рядом с геолокацией пользователя.

## Возможности

- диалог с отдельной памятью для каждого чата, пользователя и темы;
- автоматическое резюме старой части разговора;
- генерация через `/draw` или фразу «нарисуй …»;
- анализ фотографии и редактирование по подписи «измени: …»;
- распознавание голосовых сообщений без локальной перекодировки;
- поиск достопримечательностей, кафе и маршрутов после отправки геолокации;
- кнопки «Короче», «Подробнее», «Продолжить» и «Нарисовать»;
- атомарные лимиты Redis с разной стоимостью операций;
- режим для групп: ответ только на упоминание или reply;
- автоматическое удаление истории и команда `/reset`;
- структурные логи задержки, ошибок и расхода токенов.

## Запуск

Нужны Python 3.12, Redis, токен Telegram-бота и ключ OpenAI API.

```bash
cp .env.example .env
# заполните TOKEN и OPENAI_API_KEY
uv sync
docker run -d --name gopobot-redis -p 6379:6379 redis:7-alpine
uv run python -m src.bot
```

FFmpeg больше не требуется: OGG-файл из Telegram отправляется API напрямую.

Для запуска всего стека:

```bash
docker compose up --build
```

В Docker Swarm можно передать образ по тегу или digest:

```bash
GOPOBOT_IMAGE=mmakeev/gopobot@sha256:... docker stack deploy -c docker-compose.yml gopobot
```

`stop-first` в конфигурации обновления выбран намеренно: Telegram long polling не
допускает две одновременно работающие копии с одним токеном.

## Команды

| Команда | Действие |
|---|---|
| `/start` | знакомство с возможностями |
| `/help` | справка |
| `/draw описание` | создать изображение |
| `/new`, `/reset` | удалить контекст текущего диалога |
| `/memory` | показать объём и срок хранения памяти |
| `/privacy` | объяснить обработку данных |

В группе бот по умолчанию отвечает, только если его упомянули или ответили на его
сообщение. Это регулирует `GROUP_MODE`: `mentions`, `all` или `off`.

## Настройки

Полный пример находится в [.env.example](.env.example).

| Переменная | По умолчанию | Назначение |
|---|---:|---|
| `TOKEN` | обязательна | токен Telegram |
| `OPENAI_API_KEY` | обязательна | ключ OpenAI API |
| `ADMIN_USERS` | пусто | Telegram ID администраторов через запятую |
| `REDIS_URL` | `redis://localhost:6379/0` | адрес Redis |
| `LIMIT_TIME` | `86400` | окно лимита, секунд |
| `LIMIT_MESSAGES` | `20` | единиц лимита на окно |
| `TEXT_REQUEST_COST` | `1` | стоимость текста |
| `IMAGE_REQUEST_COST` | `5` | стоимость генерации/редактирования |
| `PHOTO_REQUEST_COST` | `2` | стоимость анализа фото |
| `VOICE_REQUEST_COST` | `2` | стоимость распознавания голоса |
| `CHAT_MODEL` | `gpt-5.6-luna` | модель текста и vision |
| `IMAGE_MODEL` | `gpt-image-2` | модель изображений |
| `TRANSCRIPTION_MODEL` | `gpt-4o-mini-transcribe` | модель транскрипции |
| `MAX_OUTPUT_TOKENS` | `1200` | максимальная длина ответа модели |
| `SYSTEM_PROMPT` | встроенный | системный промпт; пусто = встроенный текст |
| `MAX_HISTORY_MESSAGES` | `100` | предельное число хранимых сообщений |
| `HISTORY_CONTEXT_MESSAGES` | `20` | сообщения, отправляемые модели дословно |
| `HISTORY_SUMMARY_TRIGGER` | `30` | порог создания резюме |
| `HISTORY_TTL` | `2592000` | срок хранения после активности, секунд |
| `GROUP_MODE` | `mentions` | поведение в группах |
| `LOG_USER_CONTENT` | `false` | разрешить полный текст в логах |
| `LOCATION_RADIUS_KM` | `3` | радиус поиска мест |
| `LOCATION_WEB_SEARCH` | `true` | использовать web search для мест |

Модели вынесены в окружение, поэтому их можно менять без правки кода. Указанные
значения сохраняют экономичный профиль исходного бота.

## Архитектура

```text
Telegram handlers
  ├── text / buttons / location
  ├── photo analysis and editing
  └── voice transcription
          │
          ├── OpenAI Responses, Images and Audio APIs
          └── Redis
                ├── conversation history and summaries
                ├── rate limits
                └── temporary location choices
```

Приложение работает одним long-polling процессом. Redis использует AOF и volume.
При переносе Redis-задачи между узлами Swarm локальный volume не переносится; для
многоузлового production-кластера используйте внешний Redis или закрепление задачи.

## Проверки

```bash
uv run ruff check .
uv run pytest
docker compose config
```

CI выполняет линтер, тесты и проверку сборки Docker-образа.
