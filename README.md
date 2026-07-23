# VK Volleyball Bot

Бот для группового чата VK, который автоматически собирает участников на волейбол. MVP-реализация с минимальным набором команд: запись, отписка, запись друга по имени.

---

## Стек

- **Python** 3.12+
- **uv** — управление зависимостями и виртуальным окружением
- **vkbottle** 4.x — фреймворк для VK-ботов
- **anyio** — структурированная асинхронность
- **pydantic + pydantic-settings** — валидация конфигурации
- **ruff + basedpyright** — линтер и type checker в строгом режиме

---

## Быстрый старт

```bash
# 1. Клонирование и установка
git clone <repo-url> vk-volleyball-bot
cd vk-volleyball-bot
uv sync

# 2. Настройка окружения
cp .env.example .env
# Заполните .env: VK_TOKEN, CHAT_PEER_ID

# 3. Запуск
uv run python -m src.main
```

---

## Команды бота

| Команда | Описание |
|---|---|
| `+` или `записаться` | Записать себя |
| `-` или `отписаться` | Убрать себя |
| `+ Имя` | Записать друга |
| `- Имя` | Убрать друга |
| `список` | Показать участников |
| `?` | Справка |

---

## Документация

Подробная документация находится в папке [`docs/`](docs/):

| Файл | Что внутри |
|------|------------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Архитектура, потоки данных, конкурентность |
| [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) | Документация по модулям `src/` |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Переменные окружения, получение токена VK |
| [`docs/BOT_COMMANDS.md`](docs/BOT_COMMANDS.md) | Полный список команд с примерами диалогов |
| [`docs/TESTING.md`](docs/TESTING.md) | Как проверить работоспособность бота |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Docker, systemd, VPS, облачные платформы |
| [`docs/CLOUDRU.md`](docs/CLOUDRU.md) | Деплой на **cloud.ru** через ВМ |
| [`docs/CLOUDRU_CONTAINERAPPS.md`](docs/CLOUDRU_CONTAINERAPPS.md) | Деплой на **cloud.ru Container Apps** |

---

## Разработка

```bash
# Линтер и форматтер
uv run ruff check src
uv run basedpyright

# Тесты
uv run pytest

# Запуск локально
uv run python -m src.main
```

---

## Troubleshooting

| Проблема | Решение |
|---|---|
| Бот не отвечает | Проверь `VK_TOKEN`, `CHAT_PEER_ID`, права токена |
| `ValidationError` | Не заданы обязательные переменные в `.env` |
| Scheduler не срабатывает | Проверь `COLLECT_WEEKDAY`/`COLLECT_TIME` и часовой пояс |
| Данные теряются | Проверь `DATA_PATH` и volume (в Docker) |

---

## Дорожная карта

- [x] Inline-клавиатура VK
- [x] Напоминание в заданный день недели перед сбором
- [ ] Лимит участников и очередь ожидания
- [ ] Админ-команды
- [ ] SQLite вместо JSON
- [ ] CI/CD: GitHub Actions

---

## Лицензия

MIT
