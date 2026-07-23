# API Reference — Модули и функции

Документация по каждому модулю в `src/`. Все типы и сигнатуры взяты из исходного кода.

---

## `src/config.py`

Конфигурация приложения через `pydantic-settings`. Читает переменные окружения из `.env`.

### `Config(BaseSettings)`

```python
class Config(BaseSettings):
    vk_token: str
    chat_peer_id: int
    collect_weekday: int = 2
    collect_time: str = "10:00"
    data_path: str = "data.json"
```

| Поле | Тип | По умолчанию | Валидация | Описание |
|------|-----|--------------|-----------|----------|
| `vk_token` | `str` | — | обязательное | Токен сообщества VK |
| `chat_peer_id` | `int` | — | обязательное | `peer_id` группового чата |
| `collect_weekday` | `int` | `2` | `0 <= v <= 6` | День недели сбора (0=Пн, 6=Вс) |
| `collect_time` | `str` | `"10:00"` | формат `HH:MM`, часы 0–23, минуты 0–59 | Время анонса нового сбора |
| `remind_enabled` | `bool` | `true` | — | Включить напоминание перед сбором |
| `remind_weekday` | `int` | `0` | `0 <= v <= 6` | День недели напоминания |
| `remind_time` | `str` | `"08:00"` | формат `HH:MM` | Время напоминания |
| `data_path` | `str` | `"data.json"` | — | Путь к JSON-файлу хранилища |

### Валидаторы

```python
@field_validator("collect_weekday", "remind_weekday")
@classmethod
def _validate_weekday(cls, v: int) -> int
```

Выбрасывает `ValueError` если день недели вне диапазона `0–6`.

```python
@field_validator("collect_time", "remind_time")
@classmethod
def _validate_time(cls, v: str) -> str
```

Выбрасывает `ValueError` если время не в формате `HH:MM`, часы вне `0–23` или минуты вне `0–59`.

---

## `src/storage.py`

JSON-хранилище участников. Безопасен в рамках одного event loop: все публичные методы асинхронны и защищены `asyncio.Lock`.

### `UserEntry(BaseModel)`

Участник из VK.

| Поле | Тип | Описание |
|------|-----|----------|
| `kind` | `Literal["user"]` | Тег дискриминатора |
| `vk_id` | `int` | ID пользователя VK |
| `name` | `str` | Имя (first_name) |

### `FriendEntry(BaseModel)`

Друг, записанный по имени без VK-аккаунта.

| Поле | Тип | Описание |
|------|-----|----------|
| `kind` | `Literal["friend"]` | Тег дискриминатора |
| `name` | `str` | Имя друга |

### `Entry`

Тип-объединение: `UserEntry | FriendEntry`.

### `Storage`

#### `__init__(self, path: Path) -> None`

Загружает существующий JSON или начинает с пустым списком.

#### `_load(self) -> None`

Приватный. Десериализует JSON из `self._path` через `_StorageData.model_validate_json()`.

#### `_save(self) -> None`

Приватный. Сериализует текущий список в JSON с отступами (`indent=2`), пишет во временный файл и атомарно перемещает через `replace()`.

#### `async add_user(self, vk_id: int, name: str) -> bool`

Добавляет `UserEntry`, если `vk_id` ещё нет в списке. Возвращает `True` при успешном добавлении.

**Сложность:** O(n) — линейный поиск по `vk_id`.

#### `async remove_user(self, vk_id: int) -> bool`

Удаляет `UserEntry` по `vk_id`. Возвращает `True` если удалён.

**Сложность:** O(n).

#### `async add_friend(self, name: str) -> None`

Добавляет `FriendEntry`. Дубликаты разрешены.

**Сложность:** O(1) амортизированно.

#### `async remove_friend(self, name: str) -> bool`

Удаляет первый найденный `FriendEntry` с точным совпадением `name`. Возвращает `True` если удалён.

**Сложность:** O(n).

#### `async list_entries(self) -> list[Entry]`

Возвращает неглубокую копию списка участников.

**Сложность:** O(n).

#### `async clear(self) -> None`

Очищает список и перезаписывает файл JSON с пустым массивом `{"participants": []}`.

---

## `src/formatting.py`

Хелперы форматирования ответов бота.

### `format_entries(entries: list[Entry]) -> str`

Форматирует список участников в нумерованную строку. `FriendEntry` помечается суффиксом `(друг)`. Если список пуст — возвращает `"Пока никто не записался."`.

### `help_text() -> str`

Возвращает статический текст справки по командам.

## `src/bot.py`

Хендлеры сообщений vkbottle. Все хендлеры асинхронные (`async def`).

### `build_inline_keyboard() -> str`

Собирает inline-клавиатуру с кнопками `+`, `-`, `Список`, `Помощь` и возвращает её JSON.

### `extract_friend_name(text: str) -> str`

Извлекает и обрезает имя друга после символа `+` или `-`.

### `setup_handlers(bot: Bot, storage: Storage) -> None`

Регистрирует все хендлеры на переданном экземпляре `Bot`. Вызывается один раз при старте. Содержит 6 текстовых хендлеров + 4 callback-хендлера для inline-клавиатуры.

#### Хендлер `+` / `записаться`

**Правило:** `@bot.on.message(text=["+", "записаться"])`

1. Вызывает `bot.api.users.get(user_ids=[msg.from_id])` для получения имени.
2. Вызывает `storage.add_user(vk_id, name)`.
3. Отвечает:
   - `"Ты записался!"` — при успехе
   - `"Ты уже в списке."` — если дубликат

**Особенность:** зависит от VK API для резолва имени. Если VK API вернёт пустой список пользователей или `first_name` отсутствует — используется `"Unknown"`. Исключения `VKAPIError` (недоступность сети, невалидный токен) пробрасываются вверх и обрабатываются vkbottle.

#### Хендлер `-` / `отписаться`

**Правило:** `@bot.on.message(text=["-", "отписаться"])`

1. Вызывает `storage.remove_user(msg.from_id)`.
2. Отвечает:
   - `"Ты отписался."` — при успехе
   - `"Тебя не было в списке."` — если не найден

#### Хендлер `+ Имя`

**Правило:** `@bot.on.message(RegexRule(r"^\+\s*(.+)$"))`

1. Извлекает текст после `+` через `split("+", 1)[1].strip()`.
2. Если имя пустое — отвечает `"Укажи имя друга: + Имя"`.
3. Вызывает `storage.add_friend(name)`.
4. Отвечает `"{name} записан(а) как друг."`.

**Особенность:** регистронезависимость зависит от ввода пользователя. Фильтрация пробелов — ручная.

#### Хендлер `- Имя`

**Правило:** `@bot.on.message(RegexRule(r"^-\s*(.+)$"))`

1. Извлекает текст после `-`.
2. Вызывает `storage.remove_friend(name)`.
3. Отвечает:
   - `"{name} убран(а) из списка."` — при успехе
   - `"Такого друга не нашлось."` — если не найден

#### Хендлер `список` / `участники` / `кто идёт`

**Правило:** `@bot.on.message(text=["список", "участники", "кто идёт"])`

1. Получает `entries = storage.list_entries()`.
2. Если пусто — отвечает `"Пока никто не записался."`.
3. Иначе формирует нумерованный список:
   - `UserEntry`: `"{i}. {name}"`
   - `FriendEntry`: `"{i}. {name} (друг)"`
4. Использует `match/case` с `assert_never` для exhaustive matching.

#### Хендлер `?` / `help` / `помощь` / `команды`

**Правило:** `@bot.on.message(text=["?", "help", "помощь", "команды"])`

Отправляет список команд с пояснениями и inline-клавиатурой.

### Inline-клавиатура

Создаётся один раз в `setup_handlers` и прикрепляется ко всем ответам бота:

```
➕ (join)    ➖ (leave)
📋 Список   ❓ Помощь
```

**Конструктор:** `Keyboard(one_time=False, inline=True)`
- `Callback(label, payload={"cmd": "..."})` — callback-кнопка
- `KeyboardButtonColor.POSITIVE` / `NEGATIVE` — цвет для `+` и `-`

### Callback-хендлеры

Обрабатывают нажатия inline-кнопок через `GroupEventType.MESSAGE_EVENT`.

#### `cb_join` — `PayloadRule({"cmd": "join"})`

Логика идентична `sign_up`, но:
- `vk_id` берётся из `event.user_id`
- Обратная связь через `event.show_snackbar(...)`

#### `cb_leave` — `PayloadRule({"cmd": "leave"})`

Логика идентична `sign_off`:
- `storage.remove_user(event.user_id)`
- `event.show_snackbar(...)`

#### `cb_list` — `PayloadRule({"cmd": "list"})`

Логика идентична `show_list`:
- Результат отправляется через `event.send_message(message=..., keyboard=inline_keyboard)`

#### `cb_help` — `PayloadRule({"cmd": "help"})`

Логика идентична `help_cmd`:
- `event.send_message(message=..., keyboard=inline_keyboard)`

> **Важно:** VK callback-кнопки не отправляют текстовое сообщение в чат. Они генерируют событие `message_event`, которое ловится через `raw_event`. Обработка происходит «тихо» — пользователь видит только снэкбар или новое сообщение от бота.

---

## `src/scheduler.py`

Еженедельный планировщик сброса списка, анонса и напоминания.

### `_next_target(now: datetime.datetime, weekday: int, hour: int, minute: int) -> datetime.datetime`

Чистая функция. Вычисляет ближайший future datetime по заданному дню недели и времени. Если цель уже прошла сегодня — сдвигает на 7 дней вперёд.

### `async _send_announcement(api: API, config: Config, inline_keyboard: str) -> None`

Отправляет еженедельный анонс через `api.messages.send(...)` с inline-клавиатурой.

### `async _send_reminder(api: API, config: Config, inline_keyboard: str, storage: Storage) -> None`

Отправляет напоминание с текущим списком участников. **Не очищает** хранилище.

### `def _pick_next_event(collect_target: datetime.datetime, remind_target: datetime.datetime | None) -> tuple[datetime.datetime, str]`

Выбирает ближайшее событие из двух target'ов. Возвращает `(target, event_label)`, где `event_label` — `"collect"` или `"remind"`.

### `async _run_with_retry(label: str, operation: Callable[[], Awaitable[None]]) -> None`

Выполняет операцию с ретрай-логикой: при сбое `OSError`, `TimeoutError` или `VKAPIError` ждёт 5 минут и повторяет.

### `run_scheduler(api: API, config: Config, storage: Storage) -> NoReturn`

Бесконечный цикл с двумя событиями:

1. **Вычисление следующих целей:**
   - Берёт `now = datetime.datetime.now()` (локальное время сервера)
   - Вычисляет `collect_target` — время следующего сбора
   - Если `remind_enabled=True`, вычисляет `remind_target` — время напоминания
   - Если напоминание и сбор совпадают — напоминание сдвигается на неделю (приоритет у сбора)

2. **Выбор ближайшего события:**
   - Вызывает `_pick_next_event(collect_target, remind_target)`

3. **Ожидание:**
   - `sleep_seconds = (target - now).total_seconds()`
   - `await anyio.sleep(sleep_seconds)`

4. **Действие:**
   - **Сбор** (`event == "collect"`): `await storage.clear()` → отправляет анонс
   - **Напоминание** (`event == "remind"`): отправляет текущий список участников

5. **Обработка ошибок:**
   - При сбое `OSError`, `TimeoutError` или `VKAPIError` — логирует ошибку, ждёт 5 минут и повторяет шаг 4
   - При успехе — переходит к шагу 1

**Тип возвращаемого значения:** `NoReturn` — функция никогда не завершается нормально.

**Важно:** используется локальное время сервера (`datetime.now()` без tzinfo). Для корректной работы часовой пояс сервера должен совпадать с ожидаемым.

---

## `src/main.py`

Точка входа.

### `main() -> None`

```python
async def main() -> None:
    config = Config()
    storage = Storage(Path(config.data_path))
    bot = Bot(config.vk_token)
    setup_handlers(bot, storage)
    
    async with anyio.create_task_group() as tg:
        tg.start_soon(run_scheduler, bot.api, config, storage)
        tg.start_soon(bot.run_polling)
```

Порядок инициализации:
1. Загрузка конфигурации из `.env`
2. Инициализация хранилища (чтение существующего JSON или создание нового)
3. Создание экземпляра `Bot` с токеном
4. Регистрация хендлеров
5. Запуск двух задач в `TaskGroup`:
   - Планировщик
   - Long Poll

**Требования к окружению:**
- Python 3.12+
- Файл `.env` в рабочей директории (или переменные окружения в системе)
- Доступ в интернет для VK API

---

## Типизация и контракты

### Invariant'ы Storage

- `_entries` всегда синхронизирован с файлом `_path`
- После любого публичного метода (`add_*`, `remove_*`, `clear`) файл актуален
- `list_entries()` возвращает копию — изменение возвращённого списка не влияет на хранилище

### Контракты VK API

- `bot.api.users.get()` работает с токеном сообщества (достаточно прав `messages` и `manage`)
- `api.messages.send()` требует токен с правом `messages`
- `random_id` должен быть уникальным в рамках 24 часов для данного peer_id

### Ошибки, которые пробрасываются

| Место | Исключение | Причина | Обработка |
|-------|-----------|---------|-----------|
| `Config()` | `ValidationError` | Невалидные `.env` | Падает при старте |
| `_StorageData.model_validate_json()` | `ValidationError` | Повреждённый JSON | Падает при старте |
| `api.users.get()` | `VKAPIError` | Невалидный токен/ID | Ловится vkbottle (сообщение не отправится) |
| `api.messages.send()` | `VKAPIError` | Нет прав / бот не в чате | Ловится vkbottle |

### Потокобезопасность

`Storage` защищён `asyncio.Lock` — все публичные методы асинхронны и используют `async with self._lock`. Это делает его безопасным в рамках одного event loop, но не потокобезопасным в классическом смысле (нет блокировок для многопоточности/многопроцессорности).
