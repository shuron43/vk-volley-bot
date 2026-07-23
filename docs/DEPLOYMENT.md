# Деплой

## Требования к серверу

- Python 3.12+
- Интернет-соединение (доступ к API VK: `api.vk.com`)
- ~50 МБ RAM (в простое)
- ~100 МБ диска (без учёта логов)
- Docker-контейнер работает не от root и требует writable `/app/data`

## Локальный запуск (для тестирования)

Бота можно запустить прямо на рабочем ПК, чтобы проверить команды и интеграцию с VK:

```bash
uv run python -m src.main
```

**Ограничения локального запуска:**
- ПК должен быть включён и подключён к интернету 24/7. Сон, гибернация или смена Wi-Fi разорвут соединение с VK.
- Планировщик анонсов (`COLLECT_WEEKDAY` + `COLLECT_TIME`) сработает только если компьютер включён в нужный момент.
- Динамический IP и периодические переподключения могут вызывать кратковременные потери связи.

> Для **постоянной работы** в групповом чате используйте VPS, облако или Docker на удалённом сервере.

## Способ 1: Docker (рекомендуется)

### Dockerfile (production)

```dockerfile
# Используем официальный образ uv с Python 3.12
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Установка часового пояса (опционально — измените TZ под ваш регион)
ENV TZ=Europe/Moscow
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем lock-файл и pyproject.toml первыми для оптимизации слоёв Docker
COPY pyproject.toml uv.lock ./

# Устанавливаем production-зависимости (без dev-группы)
RUN uv sync --frozen --no-dev

# Копируем исходный код
COPY src ./src

# Создаём директорию для данных, пользователя и выдаём права на /app
RUN mkdir -p /app/data \
    && useradd --uid 1000 --create-home --home-dir /home/botuser --shell /usr/sbin/nologin botuser \
    && chown -R botuser:botuser /app
VOLUME ["/app/data"]

# По умолчанию внутри контейнера пишем в volume
ENV DATA_PATH=/app/data/participants.json

# Работаем не от root
USER botuser

# Проверяем, что volume с данными доступен для записи
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD test -w /app/data

# Запуск бота через uv run с модульным entrypoint
CMD ["uv", "run", "--no-sync", "python", "-m", "src.main"]
```

### Сборка и запуск

```bash
# Сборка образа
docker build -t vk-volleyball-bot .

# Запуск с пробросом .env
docker run -d \
  --name vk-bot \
  --env-file .env \
  -e DATA_PATH=/app/data/participants.json \
  -v vk-bot-data:/app/data \
  --restart unless-stopped \
  vk-volleyball-bot
```

### Docker Compose

```yaml
services:
  vk-bot:
    build: .
    container_name: vk-volleyball-bot
    restart: unless-stopped
    mem_limit: 256m
    cpus: '0.5'
    env_file:
      - .env
    environment:
      DATA_PATH: /app/data/participants.json
    volumes:
      - vk-bot-data:/app/data
    healthcheck:
      test: ["CMD", "test", "-w", "/app/data"]
      interval: 30s
      timeout: 5s
      start_period: 20s
      retries: 3
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  vk-bot-data:
```

```bash
docker compose up -d
docker compose logs -f
docker compose down
```

Healthcheck проверяет только writable `/app/data`. Если том смонтирован неверно или права на запись потеряны, контейнер станет unhealthy.

### Обновление

```bash
# Пересобрать и перезапустить
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Способ 2: VPS / Bare Metal

### Установка uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env
```

### Клонирование и запуск

```bash
git clone <repo-url>
cd vk-volleyball-bot
uv sync --no-dev

# Настройка окружения
cp .env.example .env
# отредактируйте .env

# Запуск в фоне
nohup uv run python -m src.main > bot.log 2>&1 &
```

### Управление через systemd

Создайте файл `/etc/systemd/system/vk-bot.service`:

```ini
[Unit]
Description=VK Volleyball Bot
After=network.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/opt/vk-volleyball-bot
Environment="PATH=/home/botuser/.cargo/bin:/usr/local/bin:/usr/bin"
ExecStart=/home/botuser/.cargo/bin/uv run python -m src.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Активация:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vk-bot.service
sudo journalctl -u vk-bot.service -f
```

Команды управления:

```bash
sudo systemctl status vk-bot
sudo systemctl restart vk-bot
sudo systemctl stop vk-bot
```

### Создание пользователя (рекомендуется)

```bash
sudo useradd -r -s /bin/false botuser
sudo mkdir -p /opt/vk-volleyball-bot
sudo chown botuser:botuser /opt/vk-volleyball-bot
```

## Способ 3: Render / Railway / Heroku

### Render

1. Создайте Web Service (или Background Worker)
2. Укажите Build Command: `uv sync --no-dev`
3. Укажите Start Command: `uv run python -m src.main`
4. Добавьте Environment Variables в панели

> ⚠️ На Render бесплатные инстансы засыпают. Для бота, который должен быть 24/7, выберите платный тариф или используйте Cron Job для пробуждения (не рекомендуется для production).

### Railway

1. Подключите GitHub-репозиторий
2. Railway автоматически определит Python-проект
3. Установите переменные окружения в Variables
4. Добавьте `NIXPACKS_UV_VERSION=0.5` если нужна конкретная версия uv

## Мониторинг

### Логи

**Docker:**
```bash
docker logs -f vk-bot
```

**systemd:**
```bash
sudo journalctl -u vk-bot.service -f
```

**Файл (nohup):**
```bash
tail -f bot.log
```

### Проверка работоспособности

Если бот не отвечает в чате:
1. Проверьте логи на ошибки авторизации VK
2. Убедитесь, что бот добавлен в чат и является администратором
3. Проверьте `CHAT_PEER_ID` — должен соответствовать реальному чату
4. Убедитесь, что у токена есть права `messages` и `manage`

### Health check (опционально)

Можно добавить простой HTTP health check, если бот запущен как web service:

```python
# Добавить в main.py
from anyio import create_tcp_listener

async def health_server():
    listener = await create_tcp_listener(local_port=8080)
    await listener.serve(lambda stream: stream.send(b"OK"))
```

Затем настройте мониторинг через UptimeRobot / Pingdom на порт 8080.
