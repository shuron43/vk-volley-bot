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

### Как получить токен и ID чата

#### VK_TOKEN

1. Открой настройки своего сообщества VK → **Работа с API** → **Ключи доступа**
2. Нажми **Создать ключ**
3. Включи разрешения:
   - ✅ **Разрешить приложению управление сообществом**
   - ✅ **Разрешить приложению доступ к сообщениям сообщества**
4. Скопируй токен и вставь в `.env`

> ⚠️ **Важно:** Не коммить `.env` в git. Файл уже добавлен в `.gitignore`.

#### CHAT_PEER_ID

**Способ 1 (простой):** Перешли любое сообщение из группового чата в личные сообщения сообщества — VK покажет `peer_id` в теле пересланного сообщения.

**Способ 2 (формула):**
```
peer_id = 2000000000 + chat_id
```
Например, если `chat_id = 1`, то `peer_id = 2000000001`.

**Способ 3 (API):**
```bash
curl "https://api.vk.com/method/messages.getConversations?access_token=$VK_TOKEN&v=5.199"
```
Ищи нужный чат в `response.items[].conversation.peer.id`.

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

Бот обрабатывает команды и callback-события только из чата, указанного в
`CHAT_PEER_ID`. События из других peer игнорируются до чтения или изменения данных.

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
uv run ruff check src tests
uv run ruff format --check src tests
uv run basedpyright

# Тесты и обязательный порог покрытия 80%
uv run pytest --cov=src --cov-report=term-missing

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
- [x] Админ-команды (очистить, удалить участника)
- [ ] Лимит участников и очередь ожидания
- [ ] SQLite вместо JSON
- [x] CI: GitHub Actions для ветки `master`

---

## Лицензия

MIT
