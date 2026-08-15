# PROJECT KNOWLEDGE BASE — VK Volleyball Bot

**Stack:** Python 3.12, uv, vkbottle 4.x, anyio, pydantic/pydantic-settings
**Layout:** src-package (imports as `from src.x import y`)
**Entry:** `src/main.py` via `anyio.run(main)`
**CI:** GitHub Actions on `master` — ruff check + format, basedpyright, pytest with coverage ≥80%, pip-audit

## STRUCTURE
```
.
├── src/          # 9 .py modules
│   ├── main.py       # orchestration root: Config → Storage → Bot → TaskGroup
│   ├── bot.py        # vkbottle handlers: 9 text + 4 callback, peer guard, admin cmds
│   ├── scheduler.py  # weekly collect/remind loop with retry
│   ├── storage.py    # JSON-backed mutable accumulator over frozen Pydantic entries
│   ├── config.py     # pydantic-settings from .env, fully validated
│   ├── formatting.py # format_entries / help_text (extracted from bot.py)
│   ├── keyboard.py   # shared inline keyboard builder
│   └── __init__.py   # package marker
├── tests/        # 94 pytest tests: 6 test files + conftest.py
├── docs/         # 8 .md files — user-facing docs (not code docs)
├── .github/workflows/ci.yml
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
| Add command | `src/bot.py` — append handler in `setup_handlers` | Text and callback handlers are paired manually; each carries a `# Duplicates cb_X — keep in sync` comment |
| Change schedule | `src/config.py` + `src/scheduler.py` | Two event types: `collect_*` (clear + announce) and `remind_*` (send current list); collision of both is rejected by `_validate_schedule_collision` |
| Change storage schema | `src/storage.py` — `UserEntry` / `FriendEntry` | Discriminated union via `kind`; add variant → update `match/case` in `src/formatting.py` (`format_entries`) |
| Add/change inline keyboard | `src/keyboard.py` — builder + 4 `cb_*` handlers in `src/bot.py` | `PayloadRule({"cmd": "..."})` on `GroupEventType.MESSAGE_EVENT`; scheduler messages reuse the same keyboard |
| Env vars / secrets | `.env` (gitignored) + `src/config.py` | `VK_TOKEN`, `CHAT_PEER_ID`, `COLLECT_WEEKDAY`, `COLLECT_TIME`, `REMIND_ENABLED`, `REMIND_WEEKDAY`, `REMIND_TIME`, `ADMIN_VK_IDS_RAW`, `DATA_PATH` |
| Admin commands | `src/bot.py` admin section + `Config.is_admin` | `очистить`/`сбросить`, `убрать Имя`/`удалить Имя`, `админ помощь`; unauthorized attempts are logged and refused |
| Docker deploy | `Dockerfile` + `docs/DEPLOYMENT.md` | Non-root user, healthcheck, resource limits; `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`; `TZ=Europe/Moscow` |

## CODE MAP
| Symbol | Type | File | Role |
|--------|------|------|------|
| `main` | async function | `src/main.py` | Entry point: initializes Config, Storage, Bot, launches TaskGroup |
| `Config` | class | `src/config.py` | pydantic-settings; validates weekdays 0–6, `HH:MM` times, data_path traversal, admin IDs, collect/remind collision |
| `Config.is_admin` | method | `src/config.py` | Checks `vk_id` against `admin_vk_ids` tuple |
| `Storage` | class | `src/storage.py` | JSON persistence; mutable wrapper over frozen `Entry` list; limits: 100 entries, 100-char names |
| `UserEntry` | pydantic model | `src/storage.py` | VK participant: `kind="user"`, `vk_id`, `name` |
| `FriendEntry` | pydantic model | `src/storage.py` | Name-only participant: `kind="friend"`, `name` |
| `Storage.remove_by_name` | async method | `src/storage.py` | Removes first entry (user or friend) matching name — backs the admin remove command |
| `setup_handlers` | function | `src/bot.py` | Registers 9 text handlers + 4 callback handlers; wraps every one in `target_peer_only`; noqa:C901,PLR0915 |
| `target_peer_only` | decorator (nested) | `src/bot.py` | Drops any message/event whose `peer_id != config.chat_peer_id` before any data read or mutation |
| `_admin_help_text` | function | `src/bot.py` | Static admin help text |
| `extract_friend_name` | function | `src/bot.py` | Trims name after `+` / `-` command sign |
| `run_scheduler` | async function | `src/scheduler.py` | Infinite loop: sleeps until the nearer of collect/remind target, then clears+announces or sends reminder |
| `_next_target` | function | `src/scheduler.py` | Pure helper: next configured weekday/time that has not elapsed |
| `_pick_next_event` | function | `src/scheduler.py` | Chooses the earlier of collect vs remind target |
| `_send_announcement` | async function | `src/scheduler.py` | Weekly collect message via VK API |
| `_send_reminder` | async function | `src/scheduler.py` | Reminder message with current participant list |
| `_run_with_retry` | async function | `src/scheduler.py` | Retry loop shared by both sends: 5-min sleep on `OSError`/`TimeoutError`/`VKAPIError`, re-raises cancellation |
| `build_inline_keyboard` | function | `src/keyboard.py` | Shared inline keyboard JSON: ➕ join, ➖ leave, 📋 list, ❓ help |
| `format_entries` | function | `src/formatting.py` | Numbered participant list with `(друг)` suffix; exhaustive `match` with `assert_never` |
| `help_text` | function | `src/formatting.py` | Static help; `compact=True` variant for callback responses |

## CONVENTIONS
- **Line length:** 88 (Ruff); `ruff format` is enforced in CI
- **Type checking:** `basedpyright` in mode `all`; a small set of rules is intentionally downgraded to `warning` with the rationale documented in `pyproject.toml` comments (vkbottle nested handlers read as unused, `Config()` called without args, exhaustiveness sentinel)
- **Lint:** Ruff `select = ["ALL"]` with explicit ignore list (see pyproject.toml)
- **Tests:** pytest with `--strict-config --strict-markers`, `filterwarnings = ["error"]`; coverage `fail_under = 80`, `branch = true`
- **Docstrings:** Google style
- **Models:** Pydantic models are `frozen=True` by default; `Config` itself is **not** frozen (exception)
- **Imports:** `from src.x import y` — the package is literally named `src`
- **Naming:** `_` prefix for intentionally unused return values (`_ = await msg.answer(...)`); `reportUnusedCallResult` is a warning
- **Storage:** All public methods are `async` and guarded by `asyncio.Lock`; `_save()` writes tempfile + `replace()` inside `asyncio.to_thread`; `_commit()` saves first and mutates in-memory state only on success, so a failed save leaves state untouched
- **Scheduler:** Resilient to VK API/network failures — `_run_with_retry` loops with a 5-minute sleep
- **Handler pairing:** Every mutating text command has a callback twin (`sign_up`↔`cb_join`, `sign_off`↔`cb_leave`, `show_list`↔`cb_list`); bodies are duplicated deliberately and marked "keep in sync"

## ANTI-PATTERNS (THIS PROJECT)
- `eval` / `exec` / `globals()` — absent; project forbids dynamic execution
- Unstructured dicts — absent; all data flows through Pydantic models
- Mutable global state — absent; `Storage` is instance-local, passed explicitly
- **Allowed with pragma:**
  - `noqa:C901,PLR0915` on `setup_handlers` — routing table is intentionally dense
  - `noqa:DTZ005` on `datetime.datetime.now()` — scheduler uses naive wall-clock time by design
  - `noqa:PLR2004` in `Config._validate_time` — magic `2` for HH:MM part lengths
  - `type:ignore[reportUnnecessaryComparison]` on the exhaustiveness sentinel in `formatting.py` — required for the `assert_never` guard

## COMMANDS
```bash
# install
uv sync

# lint / format / type-check (mirrors CI)
uv run ruff check src tests
uv run ruff format --check src tests
uv run basedpyright

# tests with mandatory 80% coverage gate
uv run pytest --cov=src --cov-report=term-missing

# dependency CVE audit (CI step)
uv run pip-audit --desc

# run locally
uv run python -m src.main

# docker
docker build -t vk-volleyball-bot .
docker run -d --env-file .env --restart unless-stopped vk-volleyball-bot
```

## NOTES
- **Concurrency:** `anyio.create_task_group()` runs `bot.run_polling()` + `run_scheduler()` together. If one crashes, both die.
- **Storage thread safety:** `Storage` has `asyncio.Lock`. Safe within one event loop; unsafe if moved to multiprocess/threaded runtime.
- **Atomic writes:** `Storage._save()` writes to a tempfile then uses `replace()` for atomic update. A crash during write leaves the original file intact; a failed save raises `OSError` and in-memory state is not mutated.
- **Storage limits:** 100 entries max, names capped at 100 chars; overflow raises `ValueError` which handlers surface as a chat reply.
- **Timezone dependency:** Scheduler relies on server local time. Dockerfile hardcodes `Europe/Moscow`.
- **Scheduler events:** Two recurring events — `collect` (clears the list, sends announcement) and `remind` (sends current list, no clear). `Config` rejects identical weekday+time for both when reminder is enabled.
- **Peer restriction:** Every handler is wrapped in `target_peer_only`; events from any chat other than `CHAT_PEER_ID` are silently ignored before reading or mutating data.
- **Admin rights:** `ADMIN_VK_IDS_RAW` is a comma-separated env var parsed into a tuple of positive ints (`NoDecode`, custom pre-validator). Admin commands refuse non-admins with a logged warning.
- **CI:** `.github/workflows/ci.yml` on push/PR to `master`: ruff check, ruff format --check, basedpyright, pytest with coverage gate, pip-audit.
- **Inline keyboard:** All bot responses include the inline keyboard; weekly announcement and reminder also include it. Callback events require `PayloadRule` handlers on `GroupEventType.MESSAGE_EVENT`.
- **Package name quirk:** The src-layout package is literally called `src`, so imports read `from src.bot import ...`. If refactored to a real package name, every import and the `Dockerfile` CMD change.
