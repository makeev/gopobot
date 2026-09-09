FROM python:3.12-slim

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv \
    && groupadd --system bot \
    && useradd --system --gid bot --home-dir /app bot

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY --chown=bot:bot src/ ./src/

USER bot

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "src.healthcheck"]

CMD ["python", "-m", "src.bot"]
