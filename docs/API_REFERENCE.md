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
    remind_enabled: bool = True
    remind_weekday: int = 0
    remind_time: str = "08:00"
    admin_vk_ids: tuple[int, ...] = ()  # env: ADMIN_VK_IDS_RAW
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
| `admin_vk_ids` | `tuple[int, ...]` | `()` | Положительные ID; `ADMIN_VK_IDS_RAW` парсится при старте | Неизменяемый набор VK ID администраторов |
| `data_path` | `str` | `"data.json"` | — | Путь к JSON-файлу хранилища |

### Methods

```python
def is_admin(self, vk_id: int) -> bool
```

Возвращает `True`, если *vk_id* присутствует в `admin_vk_ids`.

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

`_parse_admin_vk_ids` разбирает `ADMIN_VK_IDS_RAW` в tuple положительных целых
ID. `_validate_schedule_collision` запрещает включённому напоминанию совпадать
с моментом сбора.

---

## `src/storage.py`

JSON-хранилище участников и состояния регистрации. Безопасен в рамках одного event loop: все публичные методы асинхронны и защищены `asyncio.Lock`.

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

### `_StorageData(BaseModel)`

Внутренняя Pydantic-модель, описывающая содержимое JSON-файла.

| Поле | Тип | Описание |
|------|-----|----------|
| `participants` | `tuple[Annotated[Entry, Field(discriminator="kind")], ...]` | Участники текущего сбора |
| `registration_state` | `Literal["closed", "opening", "open"]` | Состояние жизненного цикла регистрации (см. `bot-user-journey-fixes` план, шаг 1) |
| `status_message_id` | `int \| None` | ID канонического статусного сообщения бота (используется callback-хендлерами для `messages.edit`) |
| `collection_id` | `int` | Номер сбора для проверки задержавшихся запросов регистрации |
| `announcement_random_id` | `int \| None` | Сохранённый идентификатор отправки незавершённого анонса |

### `Storage`

#### `__init__(self, path: Path) -> None`

Загружает существующий JSON или начинает с пустым списком и состоянием `closed`.

#### `_load(self) -> None`

Приватный. Десериализует JSON из `self._path` через `_StorageData.model_validate_json()`. Состояние `opening` сохраняется для возобновления планировщиком при старте; участники сохраняются.

#### `_save(self, data: _StorageData) -> None`

Приватный. Сериализует снимок в JSON, пишет во временный файл и атомарно перемещает через `replace()`.

#### `_commit(self, data: _StorageData) -> None`

Сначала сохраняет снимок, затем заменяет `_entries`, `_registration_state` и `_status_message_id`. При ошибке записи живое и дисковое состояния остаются прежними.

#### `_commit_entries(self, entries: list[Entry]) -> None`

Снимок только участников; состояние регистрации и `status_message_id` сохраняются.

#### `async add_user(self, vk_id: int, name: str, *, expected_collection: int | None = None) -> bool`

Хендлер передаёт номер из `active_collection()`: состояние `open` и номер сбора проверяются под той же блокировкой, что и добавление. При несовпадении — `ValueError`. Без аргумента сохраняется низкоуровневая операция добавления без проверки жизненного цикла.

Добавляет `UserEntry`, если `vk_id` ещё нет в списке. Возвращает `True` при успешном добавлении.

**Сложность:** O(n) — линейный поиск по `vk_id`.

#### `async remove_user(self, vk_id: int) -> bool`

Удаляет `UserEntry` по `vk_id`. Возвращает `True` если удалён.

**Сложность:** O(n).

#### `async add_friend(self, name: str, *, expected_collection: int | None = None) -> None`

Добавляет `FriendEntry`. Дубликаты разрешены. Хелпер `add_friend_by_name` передаёт `expected_collection` из `active_collection()`; хранилище атомарно проверяет состояние и номер сбора перед добавлением, как в `add_user`.

**Сложность:** O(1) амортизированно.

#### `async remove_friend(self, name: str) -> bool`

Удаляет первый найденный `FriendEntry` с точным совпадением `name`. Возвращает `True` если удалён.

**Сложность:** O(n).

#### `async list_entries(self) -> list[Entry]`

Возвращает неглубокую копию списка участников.

**Сложность:** O(n).

#### `async registration_state(self) -> Literal["closed", "opening", "open"]`

Текущее состояние жизненного цикла регистрации. Используется хелперами ботa, не вызывается из пользовательских команд напрямую.

#### `async status_message_id(self) -> int | None`

ID канонического статусного сообщения бота, в котором callback-кнопки редактируют список. `None`, если сообщение ещё не было отправлено или потеряно.

#### `async is_registration_open(self) -> bool`

`True`, если `registration_state == "open"`. Это единственная проверка для всех попыток добавления участника.

#### `async mark_opening(self) -> None`

Переводит регистрацию в `opening`, сохраняя участников и ID статусного сообщения. Создаёт и сохраняет `announcement_random_id`, если его ещё нет. Повторный вызов сохраняет тот же ID. После перезапуска планировщик возобновляет открытие с этим идентификатором отправки.

#### `async start_new_collection(self, status_message_id: int | None) -> None`

Атомарно очищает `participants`, переводит регистрацию в `open`, сохраняет новый `status_message_id`, увеличивает `collection_id` и сбрасывает `announcement_random_id`. После успешной отправки анонса планировщик повторяет неудачное сохранение отдельно от отправки.

#### `async set_status_message_id(self, message_id: int | None) -> None`

Сохраняет ID заменяющего сообщения, отправленного при неудачном редактировании callback-кнопки. Не меняет ни участников, ни состояние регистрации.

#### `async remove_by_name(self, name: str) -> bool`

Удаляет первую запись с точным совпадением `name` (UserEntry или FriendEntry). Возвращает `True` если удалён.

**Сложность:** O(n).

#### `async clear(self) -> None`

Очищает список и перезаписывает файл JSON с пустым массивом участников. Состояние регистрации и `status_message_id` сохраняются. Используется только админ-командой `очистить`/`сбросить`; **не вызывается планировщиком** — за это отвечает `start_new_collection()`.

---

## `src/formatting.py`

Хелперы форматирования ответов бота.

### `format_entries(entries: list[Entry]) -> str`

Форматирует список участников в нумерованную строку. `FriendEntry` помечается суффиксом `(друг)`. Если список пуст — возвращает `"Пока никто не записался."`.

### `help_text() -> str`

Возвращает статический текст справки по командам.

## `src/bot.py`

Хендлеры сообщений vkbottle. Все хендлеры асинхронные (`async def`).

### `extract_friend_name(text: str) -> str`

Извлекает и обрезает имя друга после символа `+` или `-` (`text[1:].strip()`).

### Внутренние помощники (замыкания в `setup_handlers`)

Хелперы `join_user`, `leave_user`, `add_friend_by_name`, `remove_friend_by_name`
разделяются между текстовыми командами и callback-кнопками, чтобы поведение
не разъезжалось между каналами:

| Хелпер | Сигнатура | Что делает |
|--------|-----------|-----------|
| `join_user(vk_id)` | `async -> tuple[str, bool]` | Получает `active_collection()`, тянет имя через VK API, добавляет `UserEntry` с атомарной проверкой номера сбора. Возвращает `(ответ, изменился_ли_список)` |
| `leave_user(vk_id)` | `async -> tuple[str, bool]` | Удаляет `UserEntry` по `vk_id`, без проверки состояния |
| `add_friend_by_name(name)` | `async -> str` | Получает `active_collection()`, добавляет `FriendEntry` с атомарной проверкой номера сбора |
| `remove_friend_by_name(name)` | `async -> str` | Удаляет первого друга с точным именем, без проверки состояния |
| `update_callback_message(event, message)` | `async -> bool` | Пытается `event.edit_message(...)`; при `VKAPIError` отправляет ровно одну замену через `bot.api.messages.send(...)` с `random_id=secrets.randbits(31)` и сохраняет её ID как новый канонический статус |
| `refresh_canonical_status()` | `async -> None` | Последовательно читает и публикует свежий список в известном статусном сообщении после любой успешной мутации через хендлеры. Ошибки VK и сети логируются |

### `setup_handlers(bot: Bot, storage: Storage, config: Config) -> None`

Регистрирует 9 текстовых и 4 callback-хендлера. Общий wrapper `target_peer_only`
допускает тело каждого handler только при `event.peer_id == config.chat_peer_id`.

#### Хендлер `записаться`

**Правило:** `@bot.on.message(text=["записаться"])`

1. Вызывает `join_user(msg.from_id)` (см. таблицу выше).
2. Отправляет ответ через `msg.answer(..., keyboard=inline_keyboard)`.

**Особенность:** зависит от VK API для резолва имени. Если VK API вернёт пустой список пользователей или `first_name` отсутствует — используется `"Unknown"`. `VKAPIError` перехватывается, пользователю отправляется сообщение о временной ошибке. При закрытой регистрации возвращается `Запись ещё не открыта. Дождись анонса сбора или нажми «Помощь».`.

#### Хендлер `+` (bare_plus, guidance-only)

**Правила:**
```
@bot.on.message(text=["+"])
@bot.on.message(RegexRule(r"^\+\s+$"))
```

Регистрируется **до** `add_friend` (RegexRule `^\+\s*(.+)$`), чтобы `+` (с пробелами или без) не доходил до логики записи друга.

Что происходит: возвращает подсказку

```
Чтобы записаться, нажми «Записаться» или напиши «записаться» после анонса. Для друга: + Имя.
```

Никаких вызовов `bot.api.users.get`, никаких мутаций хранилища — в любом состоянии жизненного цикла.

#### Хендлер `-` / `отписаться`

**Правило:** `@bot.on.message(text=["-", "отписаться"])`

1. Вызывает `leave_user(msg.from_id)`.
2. Отвечает через `msg.answer(...)` одним из:
   - `"Ты отписался."` — при успехе
   - `"Тебя не было в списке."` — если не найден

#### Хендлер `+ Имя`

**Правило:** `@bot.on.message(RegexRule(r"^\+\s*(.+)$"))`

1. Извлекает текст после `+` через `extract_friend_name()`.
2. Если имя пустое — отвечает `"Укажи имя друга: + Имя"` (эта ветка уже не достигается, если сработал `bare_plus`).
3. Вызывает `add_friend_by_name(name)` — внутри проверяется `active_collection()`. Если закрыто — `Запись ещё не открыта. Дождись анонса сбора или нажми «Помощь».`. Перед сохранением повторно проверяются состояние и номер сбора под блокировкой.
4. Иначе — `"{name} записан(а) как друг."`.

**Особенность:** регистр сохраняется как ввёл пользователь. Фильтрация пробелов ручная.

#### Хендлер `- Имя`

**Правило:** `@bot.on.message(RegexRule(r"^-\s*(.+)$"))`

1. Извлекает текст после `-`.
2. Вызывает `remove_friend_by_name(name)`.
3. Отвечает:
   - `"{name} убран(а) из списка."` — при успехе
   - `"Такого друга не нашлось."` — если не найден

#### Хендлер `список` / `участники` / `кто идёт`

**Правило:** `@bot.on.message(text=["список", "участники", "кто идёт"])`

1. Получает `entries = storage.list_entries()`.
2. Если пусто — отвечает `"Пока никто не записался."`.
3. Иначе формирует нумерованный список через `format_entries(entries)` (нумерация начинается с 1; `FriendEntry` помечается `(друг)`).
4. `format_entries` использует `match/case` с `assert_never` для exhaustive matching.

#### Хендлер `?` / `help` / `помощь` / `команды`

**Правило:** `@bot.on.message(text=["?", "help", "помощь", "команды"])`

Отправляет `help_text()` — единый текст справки, общий с кнопкой **❓ Помощь**. Внутри:
- объясняется жизненный цикл (запись открывается после анонса);
- явно сказано, что `+` без имени — подсказка, а не запись;
- `+ Имя` помечено как доступное только пока запись открыта.

### Inline-клавиатура

Создаётся **один раз** в `setup_handlers` и прикрепляется ко всем ответам бота
(см. `src/keyboard.py`):

```
✅ Записаться   ↩️ Отписаться
📋 Список      ❓ Помощь
```

**Конструктор:** `Keyboard(one_time=False, inline=True)`
- `Callback(label, payload={"cmd": "..."})` — callback-кнопка
- `KeyboardButtonColor.SECONDARY` — нейтральный цвет для **✅ Записаться** и **↩️ Отписаться** (визуально не выделяют «хорошие» и «плохие» действия)

`payload` остаются прежними (`join`, `leave`, `list`, `help`), чтобы не ломать существующие кнопки в диалоге — поменялись только подписи.

### Callback-хендлеры

Обрабатывают нажатия inline-кнопок через `GroupEventType.MESSAGE_EVENT`. Все четыре хелпера `target_peer_only`-обёрнуты.

#### `cb_join` — `PayloadRule({"cmd": "join"})`

Шарит `join_user` с `sign_up`:
1. `response, changed = await join_user(event.user_id)`
2. Если `changed` — обновляет канонический статус через `refresh_canonical_status()`
3. Показывает snackbar: `_ = await event.show_snackbar(response)`

#### `cb_leave` — `PayloadRule({"cmd": "leave"})`

Шарит `leave_user` с `sign_off`:
1. `response, changed = await leave_user(event.user_id)`
2. Если `changed` — обновляет канонический статус
3. Snackbar с ответом

#### `cb_list` — `PayloadRule({"cmd": "list"})`

Шарит форматирование с `show_list`:
1. `entries = storage.list_entries()`
2. `ok = await update_callback_message(event, format_entries(entries))`
3. При успехе — snackbar `"Список обновлён в сообщении бота."`

`update_callback_message` сначала пробует `event.edit_message(...)`; при `VKAPIError` ровно один раз отправляет `bot.api.messages.send(...)` с криптографически случайным `random_id` и сохраняет возвращённый ID через `storage.set_status_message_id(message_id)`.

#### `cb_help` — `PayloadRule({"cmd": "help"})`

Шарит текст с `help_cmd`:
1. `ok = await update_callback_message(event, help_text(compact=True))`
2. При успехе — snackbar `"Справка обновлена."`

> **Важно:** VK callback-кнопки не отправляют текстовое сообщение в чат. Они генерируют событие `message_event`, которое ловится через `raw_event`. При успешном редактировании пользователь видит только snackbar; чат остаётся без новых сообщений. Если VK отклонил редактирование — чат получает ровно одну замену, и её ID становится каноническим статусом для будущих обновлений.

### Админ-хендлеры

Требуют `config.is_admin(msg.from_id) == True`. Доступны только пользователям из `ADMIN_VK_IDS_RAW`. Поведение админ-команд не менялось в рамках `bot-user-journey-fixes`.

#### `admin_clear` — `text=["очистить", "сбросить"]`

- Проверяет права администратора через `config.is_admin(msg.from_id)`
- Вызывает `storage.clear()`
- Отвечает: `"Список участников очищен."`

#### `admin_remove` — `RegexRule(r"^(?:убрать|удалить)\s+(.+)$")`

- Проверяет права администратора
- Извлекает имя из текста сообщения
- Вызывает `storage.remove_by_name(name)`
- Отвечает подтверждением или `"Такого участника не нашлось."`

#### `admin_help` — `text=["админ помощь", "admin help"]`

- Проверяет права администратора
- Отправляет список админ-команд

---

## `src/scheduler.py`

Еженедельный планировщик: анонс нового сбора и напоминание. Анонс защищён
жизненным циклом регистрации (`mark_opening` → `_send_announcement` →
`start_new_collection`), так что при сбое VK участники не теряются.

### `_next_target(now: datetime.datetime, weekday: int, hour: int, minute: int) -> datetime.datetime`

Чистая функция. Вычисляет ближайший future datetime по заданному дню недели и времени. Если цель уже прошла сегодня — сдвигает на 7 дней вперёд.

### `async _send_announcement(api: API, config: Config, inline_keyboard: str) -> int | None`

Отправляет еженедельный анонс через `api.messages.send(...)` с inline-клавиатурой. Возвращает ID отправленного сообщения (`int`) или `None`, если VK вернул значение, которое не является `int` (например, массив ошибки под нагрузкой).

### `async _send_reminder(api: API, config: Config, inline_keyboard: str, storage: Storage) -> None`

Отправляет напоминание с текущим списком участников. **Не очищает** хранилище и не меняет состояние регистрации.

### `def _pick_next_event(collect_target: datetime.datetime, remind_target: datetime.datetime | None) -> tuple[datetime.datetime, str]`

Выбирает ближайшее событие из двух target'ов. Возвращает `(target, event_label)`, где `event_label` — `"collect"` или `"remind"`.

### `async _run_with_retry[T](label: str, operation: Callable[[], Awaitable[T]]) -> T`

Выполняет операцию с ретрай-логикой: при сбое `OSError`, `TimeoutError` или `VKAPIError` ждёт 5 минут и повторяет; пробрасывает `anyio.get_cancelled_exc_class()` без задержки.

### `run_scheduler(api: API, config: Config, storage: Storage) -> NoReturn`

Бесконечный цикл с двумя событиями:

1. **Вычисление следующих целей:**
   - Берёт `now = datetime.datetime.now()` (локальное время сервера)
   - Вычисляет `collect_target` — время следующего сбора
   - Если `remind_enabled=True`, вычисляет `remind_target` — время напоминания
   - Совпадающие включённые расписания невозможны: `Config` отклоняет их при старте

2. **Выбор ближайшего события:**
   - Вызывает `_pick_next_event(collect_target, remind_target)`

3. **Ожидание:**
   - `sleep_seconds = (target - now).total_seconds()`
   - `await anyio.sleep(sleep_seconds)`

4. **Действие:**
   - **Сбор** (`event == "collect"`):
     1. `await storage.mark_opening()` — переводит регистрацию в `opening`, **сохраняя** участников и `status_message_id`. Этот шаг ровно один, и до успешной отправки список не очищается.
     2. `message_id = await _run_with_retry("Weekly announcement", _send_announcement)` — повторяется каждые 5 минут при сбоях.
     3. `await storage.start_new_collection(message_id)` — атомарно очищает участников, переводит регистрацию в `open` и сохраняет ID нового анонса как канонический статус.
   - **Напоминание** (`event == "remind"`): отправляет текущий список участников без касания состояния.

5. **Обработка ошибок:**
   - Сбои `OSError`, `TimeoutError` или `VKAPIError` при отправке логируются, затем попытка повторяется через 5 минут. Для анонса используется сохранённый `random_id`. Состояние `opening` восстанавливается при старте через `_finish_opening()`. Ошибка сохранения после успешного анонса повторяет только активацию сбора.
   - При успехе — переходит к шагу 1.

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
    setup_handlers(bot, storage, config)
    
    async with anyio.create_task_group() as tg:
        _ = tg.start_soon(run_scheduler, bot.api, config, storage)
        _ = tg.start_soon(bot.run_polling)
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

- `_entries`, `_registration_state` и `_status_message_id` всегда синхронизированы с файлом `_path` после успешной записи
- Ошибка сохранения не публикует снимок-кандидат в живое состояние
- После любого публичного метода (`add_*`, `remove_*`, `clear`, `mark_opening`, `start_new_collection`, `set_status_message_id`) файл актуален
- `list_entries()` возвращает копию — изменение возвращённого списка не влияет на хранилище
- `_load()` сохраняет `opening` и идентификатор отправки; планировщик возобновляет незавершённое открытие до ожидания расписания

### Контракты VK API

- `bot.api.users.get()` работает с токеном сообщества (достаточно прав `messages` и `manage`)
- `api.messages.send()` требует токен с правом `messages`
- Для анонса ненулевой `random_id` сохраняется до отправки и повторно используется после ошибок и перезапуска. Напоминания и callback-fallback генерируют отдельные случайные идентификаторы.

### Ошибки, которые пробрасываются

| Место | Исключение | Причина | Обработка |
|-------|-----------|---------|-----------|
| `Config()` | `ValidationError` | Невалидные `.env` | Падает при старте |
| `_StorageData.model_validate_json()` | `ValidationError` | Повреждённый JSON | Падает при старте |
| `api.users.get()` | `VKAPIError` | Невалидный токен/ID | `join_user` возвращает `"Не удалось получить данные из VK. Попробуй позже."`, `sign_up` отвечает этим текстом, `cb_join` показывает его в snackbar |
| `api.messages.send()` в scheduler | `VKAPIError` | Нет прав / временный сбой | `_run_with_retry` повторяет через 5 минут |
| `event.edit_message()` в `update_callback_message` | `VKAPIError` | Сообщение слишком старое или удалено | Один fallback на `messages.send` + `set_status_message_id`, чат получает ровно одну замену |
| `bot.api.messages.edit()` в `refresh_canonical_status` | `VKAPIError` | Канонический статус устарел | Логируется, чат не получает нового сообщения; следующая попытка после очередной мутации |

### Жизненный цикл регистрации

Состояние регистрации и `status_message_id` хранятся в одном JSON с участниками
и обновляются атомарно. Это даёт три гарантии:

1. **Участники не теряются при сбое анонса.** `mark_opening` сохраняет их до
   отправки, и `_load` сохраняет прерванное `opening` для восстановления.
2. **Очистка и открытие происходят одним коммитом.** `start_new_collection`
   единственный метод, который и стирает список, и переключает состояние.
3. **Callback-кнопки редактируют канонический статус.** Если редактирование
   не удалось, новая замена сохраняется через `set_status_message_id`, и
   дальнейшие правки идут уже в неё.

### Потокобезопасность

`Storage` защищён `asyncio.Lock` — все публичные методы асинхронны и используют `async with self._lock`. Это делает его безопасным в рамках одного event loop, но не потокобезопасным в классическом смысле (нет блокировок для многопоточности/многопроцессорности).
