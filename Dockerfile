# Используем официальный образ uv с Python 3.12
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Установка часового пояса (опционально — измените TZ под ваш регион)
ENV TZ=Europe/Moscow
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Создаём не-root пользователя и директорию для данных до COPY
RUN useradd --uid 1000 --create-home --home-dir /home/botuser --shell /usr/sbin/nologin botuser \
    && mkdir -p /app/data \
    && chown botuser:botuser /app/data

# Копируем lock-файл и pyproject.toml первыми для оптимизации слоёв Docker
COPY --chown=botuser:botuser pyproject.toml uv.lock ./

# Устанавливаем production-зависимости (без dev-группы)
RUN uv sync --frozen --no-dev

# Копируем исходный код
COPY --chown=botuser:botuser src ./src

VOLUME ["/app/data"]

# По умолчанию внутри контейнера пишем в volume
ENV DATA_PATH=/app/data/participants.json

# Работаем не от root
USER botuser

# Проверяем, что volume с данными доступен для записи
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD test -w /app/data

# Запуск бота через uv run с модульным entrypoint
CMD ["uv", "run", "--no-sync", "python", "-m", "src.main"]
