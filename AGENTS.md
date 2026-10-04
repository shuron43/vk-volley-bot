# PROJECT KNOWLEDGE BASE — VK Volleyball Bot

**Stack:** Python 3.12, uv, vkbottle 4.x, anyio, pydantic/pydantic-settings.
**Package:** imports use `from src.x import y`; entry is `src/main.py` via
`anyio.run(main)`. CI on `main`: Ruff, basedpyright, pytest with branch coverage
≥80%, pip-audit.

## DEVELOPMENT RULES — READ FIRST

- Before development, read [Piecemeal Growth](docs/mode-piecemeal-growth.md) and
  [Focused Specs](docs/focused-specs.md). Apply both throughout implementation
  and review, using [the project workflow](docs/DEVELOPMENT.md).
- Start from the current user request, executable requirement or observed
  failure. Make the smallest change that meets that need; introduce generality
  only when present use requires it. Preserve integrity at real boundaries.
- The application must satisfy the current BDD. Business specs have one request
  and one user-visible or durable `Then`, through the product boundary. For bot
  behavior use the actual router, keyboard payload, storage and card publisher;
  mock the external VK API. Keep infrastructure contracts separate.
- Work in a separate `codex/` branch. Inspect Git state and preserve unrelated
  work; reuse the task's existing branch when appropriate.
- `docs/` is current knowledge. The complete catalog below is always in agent
  context; read the documents relevant to the task, rather than loading all files.
  Keep this catalog and `docs/README.md` current when docs are added/moved/removed.
- `records/` is dated history, outside routine startup and repository searches.
  Exclude `records/**` from project-wide discovery with `rg -g '!records/**'`.
  Read/search a specific record only for a user request or a concrete need for
  historical evidence in the current task; state that need first. Old plans do
  not create current tasks. Do not update historical code names, commands or
  results after a refactor; record new findings separately.
- Temporary inline memory is allowed now, using only standalone comments
  `# AGENT-NOTE: <observed surprise or local constraint>; <reason/evidence>`.
  Do not tag obvious code, future plans or rules already in docs. Find all notes
  with `rg -n '^[ \t]*# AGENT-NOTE:' src tests scripts`. When knowledge stabilizes
  or duplicates accumulate, validate, deduplicate, promote it to docs and remove
  the notes. Bulk removal and the lifecycle are in `docs/DEVELOPMENT.md`.

## CURRENT DOCUMENT CATALOG

| Document | Read for |
|---|---|
| [docs/README.md](docs/README.md) | Navigation through current knowledge |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Development workflow, docs/records boundary, inline memory |
| [docs/mode-piecemeal-growth.md](docs/mode-piecemeal-growth.md) | Scope and design decisions; required before development |
| [docs/focused-specs.md](docs/focused-specs.md) | Behavior specs and their review; required before development |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Data flow, event lifecycle, concurrency and persistence |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Current module contracts and API |
| [docs/BOT_COMMANDS.md](docs/BOT_COMMANDS.md) | User/admin commands, card behavior and registration rules |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Env settings, VK IDs, schedule validation and timezone |
| [docs/TESTING.md](docs/TESTING.md) | Automated checks and manual acceptance procedures |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker, systemd, VPS and general deployment |
| [docs/PROXMOX_LXC.md](docs/PROXMOX_LXC.md) | Ubuntu LXC on Proxmox, systemd and backups |
| [docs/CLOUDRU.md](docs/CLOUDRU.md) | cloud.ru VM deployment |
| [docs/CLOUDRU_CONTAINERAPPS.md](docs/CLOUDRU_CONTAINERAPPS.md) | cloud.ru Container Apps deployment |

## WHERE TO LOOK

| Task | Location and current contract |
|---|---|
| Entry/orchestration | `src/main.py`: Config → Storage → Bot → shared CardPublisher → TaskGroup |
| Commands and permissions | `src/bot.py`: `setup_handlers`, peer guard, private admin replies; signup/withdrawal text and callback paths share helpers |
| Cards and VK IDs | `src/cards.py`: one shared publisher, persisted `message_id` and `conversation_message_id`, serialized replacement/edit/delete |
| Schedule and manual events | `src/config.py` + `src/scheduler.py`: collect/remind and saved event close deadline; `open_manual_event` shares the publisher |
| Storage schema | `src/storage.py`: frozen `UserEntry`/`FriendEntry`, registration snapshot, atomic transitions and late withdrawal/restore |
| Text and keyboard | `src/formatting.py` + `src/keyboard.py`: current event card, help, three inline callback actions |
| Setup IDs without running bot | `src/vk_ids.py`: community Long Poll probe and profile lookup |
| Behavior specs | `tests/test_bot_handlers.py`: real routed requests and durable outcomes; other test responsibilities are in `docs/TESTING.md` |
| Runtime scripts | `scripts/`: platform-specific start/stop, PID, logs and process lock |
| Deployment | `Dockerfile`, `docker-compose.yml`, `.env.example`, deployment docs |

## CONVENTIONS AND CURRENT CONSTRAINTS

- Ruff selects `ALL`, line length 88, Google docstrings. Explicit exceptions
  live in `pyproject.toml`; `setup_handlers` may use `noqa:C901,PLR0915`.
- Basedpyright uses `all` for `src/`. Intentional warnings for framework
  registrations, settings constructors and the exhaustiveness sentinel have
  their rationale in `pyproject.toml`.
- Pytest uses strict config/markers and warnings as errors. Coverage is branch
  coverage with a mandatory 80% floor. Preserve meaningful boundary cases.
- Pydantic data models are frozen; `Config` is intentionally mutable. Use typed
  models at data boundaries. Do not use `eval`, `exec`, `globals()` or mutable
  global application state.
- `_` names intentionally unused call results. Imports are literally `src.*`;
  changing the package name also affects Docker's entry command.
- `Storage` serializes mutations with `asyncio.Lock`; `_commit()` saves before
  changing memory. `_save()` writes a tempfile and replaces the file in a thread.
  A failed save leaves live and durable state intact. Limits are 100 entries and
  100-character names. Locks assume one event loop.
- `CardPublisher` must be shared by handlers and scheduler. Save the new card's
  identity before deleting the old card. VK deletion refusals must be observed.
- Registration lifecycle is `closed → opening → open → closed`; opening metadata
  is persisted before sending. Retry uses the saved `random_id` and deadline.
  Recovery closes legacy active state without a deadline, preserving attendance.
- At event start new identities are refused. Existing rows may withdraw/restore
  during the first 30 minutes; withdrawn rows remain marked `(-)` below active
  rows and are excluded from the count. At exactly 30 minutes changes are refused.
- Scheduler uses server local wall-clock time; Docker uses `Europe/Moscow`.
  `datetime.now()` has the intentional `noqa:DTZ005`. Network/API failures retry
  after five minutes; cancellation propagates. Bot and scheduler share an anyio
  task group, so an uncaught failure stops both.
- Every text/callback handler ignores other peers before data access. Admin
  commands require positive user IDs from `ADMIN_VK_IDS_RAW`; an empty list
  disables access for everyone. Commands are deleted before their action;
  admin help, status and errors are private.
- Participant text commands move the current card last; callbacks edit it in
  place and use snackbar feedback. Buttons are signup, withdrawal and help.

## COMMANDS

```bash
uv sync
uv run ruff check src tests
uv run ruff format --check src tests
uv run basedpyright
uv run pytest --cov=src --cov-report=term-missing
uv run pip-audit --desc
uv run python -m src.main
```

For documentation-only changes, verify local links, catalog coverage and
`git diff --check`; do not add mirrored tests or run the bot to check Markdown.
Never commit `.env`, tokens, runtime JSON or logs.
