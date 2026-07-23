# PROJECT KNOWLEDGE BASE — VK Volleyball Bot

**Stack:** Python 3.12, uv, vkbottle, anyio, pydantic/pydantic-settings
**Layout:** src-package (imports as `from src.x import y`)
**Entry:** `src/main.py` via `anyio.run(main)`

## STRUCTURE
```
.
├── src/          # 6 .py modules, ~400 LOC
│   ├── main.py      # orchestration root: Config → Storage → Bot → TaskGroup
│   ├── bot.py       # vkbottle message handlers (inline, regex)
│   ├── scheduler.py # weekly reset + announce loop
│   ├── storage.py   # JSON-backed mutable accumulator over frozen Pydantic entries
│   ├── config.py    # pydantic-settings from .env
│   └── __init__.py  # package marker
├── tests/        # smoke tests (pytest)
├── docs/         # 8 .md files — user-facing docs (not code docs)
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Add command | `src/bot.py` — append handler in `setup_handlers` | Regex handlers for `+ Name` / `- Name`; exact text for the rest |
| Change schedule | `src/config.py` + `src/scheduler.py` | `collect_time` is `str` validated as `HH:MM` (0-23 hours, 0-59 minutes) |
| Change storage schema | `src/storage.py` — `UserEntry` / `FriendEntry` | Discriminated union via `kind`; add variant → update all `match/case` in `bot.py` |
| Add/change inline keyboard | `src/bot.py` — `inline_keyboard` builder + 4 `cb_*` handlers | `PayloadRule({"cmd": "..."})` for callback events; also update `src/scheduler.py` weekly message |
| Env vars / secrets | `.env` (gitignored) + `src/config.py` | `VK_TOKEN`, `CHAT_PEER_ID`, `COLLECT_WEEKDAY`, `COLLECT_TIME`, `DATA_PATH` |
| Docker deploy | `Dockerfile` + `docs/DEPLOYMENT.md` | Non-root user, healthcheck, resource limits; uses `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`; sets `TZ=Europe/Moscow` |

## CODE MAP
| Symbol | Type | File | Role |
|--------|------|------|------|
| `main` | async function | `src/main.py` | Entry point: initializes Config, Storage, Bot, launches TaskGroup |
| `Config` | class | `src/config.py` | pydantic-settings; reads `.env`; validates `collect_weekday` 0–6 |
| `Storage` | class | `src/storage.py` | JSON persistence; mutable wrapper over frozen `Entry` list |
| `UserEntry` | pydantic model | `src/storage.py` | VK participant: `kind="user"`, `vk_id`, `name` |
| `FriendEntry` | pydantic model | `src/storage.py` | Name-only participant: `kind="friend"`, `name` |
| `setup_handlers` | function | `src/bot.py` | Registers 6 text handlers + 4 callback handlers + inline keyboard; noqa:C901,PLR0915 |
| `run_scheduler` | async function | `src/scheduler.py` | Infinite loop: sleep until next weekday/time → `storage.clear()` → announce with inline keyboard; retries on VK API failure every 5 min |
| `build_inline_keyboard` | function | `src/bot.py` | Builds shared inline keyboard JSON for all bot responses |
| `format_entries` | function | `src/bot.py` | Formats participant list into numbered string with `(друг)` suffix |
| `help_text` | function | `src/bot.py` | Returns static help message text |
| `extract_friend_name` | function | `src/bot.py` | Trims name after `+` / `-` command sign |
| `_next_target` | function | `src/scheduler.py` | Pure helper: computes next target datetime from now + weekday/time config |
| `_send_announcement` | async function | `src/scheduler.py` | Sends weekly announcement message via VK API |

## CONVENTIONS
- **Line length:** 88 (Ruff)
- **Type checking:** `basedpyright` in mode `all` — zero tolerance
- **Lint:** Ruff `select = ["ALL"]` with explicit ignore list (see pyproject.toml)
- **Docstrings:** Google style
- **Models:** Pydantic models are `frozen=True` by default; `Config` itself is **not** frozen (exception)
- **Imports:** `from src.x import y` — the package is literally named `src`
- **Naming:** `_` prefix for intentionally unused return values (`_ = self._path.write_text(...)`)
- **Storage:** All public methods are `async` and guarded by `asyncio.Lock`; `_save()` uses tempfile + `replace()` for atomic writes
- **Scheduler:** Resilient to VK API failures — inner retry loop with 5-minute sleep

## ANTI-PATTERNS (THIS PROJECT)
- `eval` / `exec` / `globals()` — absent; project forbids dynamic execution
- Unstructured dicts — absent; all data flows through Pydantic models
- Mutable global state — absent; `Storage` is instance-local, passed explicitly
- **Allowed with pragma:**
  - `noqa:C901` on `setup_handlers` — routing table is intentionally dense
  - `noqa:DTZ005` on `datetime.datetime.now()` — scheduler uses naive wall-clock time by design
  - `type:ignore[reportUnnecessaryComparison]` on exhaustiveness sentinel — required for `assert_never` guard

## COMMANDS
```bash
# install
uv sync

# lint / type-check
uv run ruff check src
uv run basedpyright

# tests
uv run pytest

# run locally
uv run python -m src.main

# docker
docker build -t vk-volleyball-bot .
docker run -d --env-file .env --restart unless-stopped vk-volleyball-bot
```

## NOTES
- **Concurrency:** `anyio.create_task_group()` runs `bot.run_polling()` + `run_scheduler()` together. If one crashes, both die.
- **Storage thread safety:** `Storage` has `asyncio.Lock`. Safe within one event loop; unsafe if moved to multiprocess/threaded runtime.
- **Atomic writes:** `Storage._save()` writes to a tempfile then uses `replace()` for atomic update. A crash during write leaves the original file intact.
- **Timezone dependency:** Scheduler relies on server local time. Dockerfile hardcodes `Europe/Moscow`.
- **No CI/CD:** pytest and tests/ exist (`tests/test_smoke.py`), but no GitHub Actions workflow yet.
- **Inline keyboard:** All bot responses include inline keyboard with `+`, `-`, `list`, `help` buttons. Weekly scheduler announcement also includes it. Callback events require `PayloadRule` handlers on `GroupEventType.MESSAGE_EVENT`.
- **Package name quirk:** The src-layout package is literally called `src`, so imports read `from src.bot import ...`. If refactored to a real package name, every import and the `Dockerfile` CMD change.
