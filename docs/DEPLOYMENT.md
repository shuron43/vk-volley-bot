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
uv run ruff check src tests
uv run ruff format --check src tests
uv run basedpyright --level error
uv run pytest --cov=src --cov-report=term-missing
uv run python -m src.main
```

Перед production-запуском `Config` должен успешно разобрать `.env`. В частности,
включённое напоминание не может совпадать со сбором, а `ADMIN_VK_IDS_RAW`
должен содержать только положительные целые ID через запятую.

**Ограничения локального запуска:**
- ПК должен быть включён и подключён к интернету 24/7. Сон, гибернация или смена Wi-Fi разорвут соединение с VK.
- Планировщик анонса (`COLLECT_*`) и автоматического закрытия (`EVENT_*`) работает, пока компьютер включён; просроченное открытое событие закроется после следующего запуска.
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
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Создаём не-root пользователя и директорию для данных до COPY
RUN useradd --uid 1000 --create-home --home-dir /home/botuser --shell /usr/sbin/nologin botuser \
    && mkdir -p /app/data

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

### Длительный локальный запуск в Windows

Для реального теста не оставляйте бота привязанным к временной терминальной
сессии. Скрипт запускает скрытый процесс, сохраняет PID и перенаправляет вывод
в постоянные файлы:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_bot.ps1
Get-Content logs/bot.stderr.log -Wait
```

PID находится в `.run/bot.pid`, стандартный вывод — в
`logs/bot.stdout.log`, журнал и ошибки — в `logs/bot.stderr.log`. Повторный
запуск скрипта не создаёт второй процесс, если сохранённый PID ещё работает.

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

### Подготовка пользователя и установка uv

```bash
sudo useradd --system --create-home --home-dir /home/botuser \
  --shell /usr/sbin/nologin botuser
sudo mkdir -p /opt/vk-volleyball-bot
sudo chown botuser:botuser /opt/vk-volleyball-bot

# Установка в системный каталог без изменения shell-профилей
curl -LsSf https://astral.sh/uv/install.sh \
  | sudo env UV_UNMANAGED_INSTALL=/usr/local/bin sh
uv --version
```

### Клонирование и запуск

```bash
sudo -u botuser git clone <repo-url> /opt/vk-volleyball-bot
cd /opt/vk-volleyball-bot
sudo -u botuser /usr/local/bin/uv sync --frozen --no-dev

# Настройка окружения
sudo -u botuser cp .env.example .env
# отредактируйте .env
```

Официальный установщик `uv` поддерживает `UV_UNMANAGED_INSTALL`: бинарник
попадает прямо в указанный каталог и установщик не меняет shell-профили. Это
удобнее для systemd, где интерактивный профиль пользователя не загружается.

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
Environment="PATH=/usr/local/bin:/usr/bin"
ExecStart=/usr/local/bin/uv run --frozen --no-dev python -m src.main
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

## Способ 3: Render / Railway

### Render

1. Создайте **Background Worker**, а не Web Service: бот не слушает HTTP-порт
2. Укажите Build Command: `uv sync --no-dev`
3. Укажите Start Command: `uv run python -m src.main`
4. Добавьте Environment Variables в панели

> ⚠️ На Render бесплатные инстансы засыпают. Для бота, который должен быть 24/7, выберите платный тариф или используйте Cron Job для пробуждения (не рекомендуется для production).

### Railway

1. Подключите GitHub-репозиторий
2. Railway автоматически определит Python-проект
3. Установите переменные окружения в Variables
4. Укажите Start Command: `uv run --frozen --no-dev python -m src.main`

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
2. Убедитесь, что бот добавлен в чат и имеет доступ ко всей переписке
3. Проверьте `CHAT_PEER_ID` — должен соответствовать реальному чату
4. Убедитесь, что у токена есть права `messages` и `manage`

### Health check

HTTP health endpoint в приложении отсутствует. Встроенный Docker healthcheck
проверяет только доступность `/app/data` для записи; фактическое подключение к
VK проверяйте по логам и контрольной команде в целевом чате.
