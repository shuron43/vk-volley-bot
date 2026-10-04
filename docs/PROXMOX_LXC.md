# Запуск VK Volleyball Bot в Ubuntu 24.04 LXC на Proxmox

Инструкция рассчитана на **уже созданный LXC с Ubuntu 24.04 LTS** и установку
из ветки `main` репозитория `https://github.com/shuron43/vk-volley-bot.git`.
Бот использует системный Python 3.12, зависимости из `uv.lock` и службу systemd.
В Ubuntu 24.04 пакет `python3` относится к ветке Python 3.12.
[Пакет Python в Ubuntu](https://packages.ubuntu.com/noble/python3).

Все команды ниже, кроме явно отмеченных команд **на узле Proxmox**, выполняй
в **Console контейнера**, войдя как `root`. `sudo` в этих командах не нужен.

## Зачем нужны пользователь, длинные команды и systemd

### Отдельный пользователь: нужны ли эти сложности

Для работы кода отдельный пользователь не обязателен: бот может запуститься
и от root. Здесь `botuser` выбран, чтобы процесс имел только обычные права.
Например, ошибка в коде или библиотеке под root может изменить системные файлы
контейнера; у `botuser` на них нет прав. LXC ограничивает процесс относительно
узла Proxmox, а пользователь ограничивает его внутри Ubuntu. Эти границы
дополняют друг друга. Для непривилегированного LXC root внутри контейнера
сопоставляется с непривилегированным UID снаружи — это не даёт самому боту
ограниченных прав внутри контейнера.
[Как устроены непривилегированные LXC](https://github.com/proxmox/pve-docs/blob/master/pct.adoc#unprivileged-containers).

`botuser` — техническая учётная запись Linux. Её не нужно добавлять в админы VK,
выдавать ей sudo или задавать пароль. `/usr/sbin/nologin` запрещает обычный вход
в shell, но systemd и root через `runuser` всё равно могут запустить от неё
конкретную программу. Домашний каталог нужен для кеша uv. Это первоначальная
настройка: при дальнейшей работе команды короткие — `systemctl start vk-bot`,
`systemctl stop vk-bot` и `systemctl restart vk-bot`.
[Управление пользователями Ubuntu](https://ubuntu.com/server/docs/how-to/security/user-management/).

Если контейнер выделен только под бота, запуск от root технически возможен,
но процесс получит полномочия администратора всей Ubuntu в этом контейнере.
Для постоянной работы здесь оставлен служебный пользователь.

### Почему команды длинные

В установочных командах явно указаны пользователь, версия Python и права
на каталоги. Например:

```bash
runuser -u botuser -- uv sync --frozen --no-dev --python 3.12
```

| Часть | Зачем нужна |
|---|---|
| `runuser -u botuser --` | Выполнить последующую команду от `botuser`, хотя в Console ты root; `--` отделяет параметры `runuser` от самой команды |
| `uv sync` | Создать `.venv` и установить зависимости проекта |
| `--frozen` | Использовать существующий `uv.lock`, не пересчитывая и не изменяя его |
| `--no-dev` | Не устанавливать pytest, ruff и другие инструменты разработки на рабочий сервер |
| `--python 3.12` | Выбрать проверенную проектом ветку Python; в Ubuntu 24.04 она уже установлена через apt |

Обычный `uv sync` тоже работает, но допускает обновление lock-файла и включает
зависимости разработки. Параметры делают установку сервера определённой.
[Параметры uv sync](https://docs.astral.sh/uv/concepts/projects/sync/).

Символ `\` в конце строки означает перенос одной длинной команды на следующую
строку, а не отдельную команду. В инструкции можно копировать блок целиком.
Полные пути в `ExecStart` нужны, чтобы служба всегда запускала Python из `.venv`
именно этого проекта. В ручных командах ниже достаточно `uv`, поскольку он
установлен в стандартный `/usr/local/bin`.

`install -d -o botuser -g botuser -m 0750 ...` одновременно создаёт каталог,
задаёт владельца, группу и права. Это заменяет отдельные `mkdir`, `chown`, `chmod`.
`0750` даёт владельцу полный доступ, группе чтение/проход, остальным — ничего;
`0700` оставляет доступ только владельцу, `0600` — чтение/запись файла владельцу.
Root при этом сохраняет административный доступ.

### Почему служба не вызывает scripts/start_bot.sh

Скрипт запуска уже решает задачу ручного фонового запуска: выполняет
`uv sync --frozen`, запускает Python через `nohup`, сохраняет PID и пишет
логи в файлы. Скрипт остановки проверяет PID и завершает этот процесс.

Для службы systemd эти обязанности выполняет сама система:

| Возможность | Скрипты start/stop | Служба из этой инструкции |
|---|---|---|
| Запуск в фоне | Да, через `nohup` | Да, процесс запускает systemd |
| Запуск после загрузки контейнера | Сам по себе не настроен | `systemctl enable vk-bot` |
| Перезапуск после завершения процесса | Нет | `Restart=always` |
| Остановка | Скрипт по PID-файлу | `systemctl stop vk-bot` |
| Логи | `logs/bot.stdout.log`, `logs/bot.stderr.log` | `journalctl -u vk-bot` |
| Установка зависимостей при запуске | Да; включая dev-зависимости | Нет; `uv sync --no-dev` выполняется отдельно при установке/обновлении |

`start_bot.sh` отправляет Python в фон и сам заканчивает работу. У службы
`Type=simple` главный процесс должен оставаться запущенным, поэтому в `ExecStart`
указан непосредственно Python. Иначе systemd отслеживала бы закончившийся скрипт,
а её остановка/перезапуск не соответствовали бы жизненному циклу бота.
Для фонового shell-скрипта потребовалась бы другая схема службы с дополнительным
учётом PID. Здесь прямой запуск проще.
[Модель процессов systemd](https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html).

Скрипты остаются пригодными для ручной проверки; ниже есть вариант их применения.
Systemd обеспечивает постоянную работу. Одновременно выбирай один способ запуска.

### Зачем разделять код и данные

`/opt/vk-volleyball-bot` содержит код и `.venv`, а `/var/lib/vk-volleyball-bot`
содержит JSON участников и состояние события. Такое разделение позволяет
заменить каталог кода, сохранив данные. Это организационный выбор, не требование
бота: можно использовать `DATA_PATH=data.json` и хранить JSON рядом с кодом.
Но тогда копируй этот файл перед заменой каталога проекта. Основные шаги ниже
сохраняют разделение, чтобы существующие данные пережили переустановку кода.

## 1. Проверить существующий контейнер

Новый контейнер создавать не нужно. В **Console твоего LXC** проверь:

```bash
cat /etc/os-release
```

Ожидается Ubuntu и `VERSION_ID="24.04"`. Если Console открыта под обычным
пользователем, сначала выполни `sudo -i`; дальнейшие административные команды
показаны для root.

В Proxmox включи **Options → Start at boot → Yes**. Для этого бота можно выделить
1 ядро, 1024 MiB RAM и 8 GiB диска — это стартовые ориентиры, менять параметры
работающего контейнера без необходимости не требуется. Проверь сетевой мост,
адрес, шлюз и DNS. Боту нужен исходящий HTTPS к VK, GitHub, PyPI и Astral;
входящий HTTP-порт он не слушает. Docker и nesting для этой инструкции не нужны.
[Настройки контейнера Proxmox](https://github.com/proxmox/pve-docs/blob/master/pct.adoc).

При входе с узла Proxmox выполни **на узле**, заменив `120` на свой CT ID:

```bash
pct enter 120
```

После `pct enter` дальнейшие команды выполняются внутри контейнера.

## 2. Подготовить Ubuntu и время

В **Console контейнера**, от root:

```bash
apt update
apt upgrade -y
apt install -y ca-certificates curl git nano tzdata util-linux python3 python3-venv

python3 --version
timedatectl set-timezone Europe/Moscow
date '+%d.%m.%Y %H:%M:%S %Z'
```

Бот использует местное время для анонса и закрытия записи. Проверь, что команда
`date` показывает правильные московские дату и время. LXC использует ядро узла;
если время неверно, проверь также часы и синхронизацию **на узле Proxmox**.
Устанавливать собственную службу NTP внутрь контейнера для этого не нужно.

Проверь сеть:

```bash
getent hosts api.vk.com
curl -I --connect-timeout 10 https://api.vk.com
```

Любой HTTP-ответ подтверждает доступ к серверу; эти команды не проверяют токен
VK. При ошибке DNS/таймауте проверь мост, адрес, шлюз, DNS и правила исходящего
трафика Proxmox Firewall, затем продолжай установку.

## 3. Установить uv, проект и Python

Один раз создай служебного пользователя и каталоги:

```bash
useradd --system --user-group --create-home --home-dir /home/botuser \
  --shell /usr/sbin/nologin botuser
install -d -o botuser -g botuser -m 0750 /opt/vk-volleyball-bot
install -d -o botuser -g botuser -m 0700 /var/lib/vk-volleyball-bot
```

В `useradd` параметры означают:

- `--system` — служебная учётная запись, а не новый человек, работающий в системе;
- `--user-group` — отдельная группа с тем же именем;
- `--create-home --home-dir /home/botuser` — домашний каталог для кеша uv;
- `--shell /usr/sbin/nologin` — запрет обычного интерактивного входа.

Если `botuser` уже существует после предыдущей попытки установки, повторять
`useradd` не нужно; проверь пользователя через `id botuser` и продолжай.

Установи uv в системный каталог:

```bash
curl -LsSf https://astral.sh/uv/install.sh \
  | env UV_UNMANAGED_INSTALL=/usr/local/bin sh
/usr/local/bin/uv --version
```

`UV_UNMANAGED_INSTALL` задаёт каталог бинарников без изменения shell-профилей.
[Официальная инструкция uv](https://docs.astral.sh/uv/reference/installer/).

Клонируй проект и установи зависимости от имени `botuser`:

```bash
runuser -u botuser -- git clone --branch main --single-branch \
  https://github.com/shuron43/vk-volley-bot.git /opt/vk-volleyball-bot

cd /opt/vk-volleyball-bot
runuser -u botuser -- uv sync --frozen --no-dev --python 3.12
runuser -u botuser -- .venv/bin/python --version
```

Ожидаемый результат последней команды — `Python 3.12.x`. uv использует
системный Python Ubuntu, создаёт `.venv` и устанавливает туда зависимости.
`.venv` изолирует библиотеки проекта, чтобы не менять Python-пакеты самой Ubuntu.
Отдельный `uv python install 3.12` в этой инструкции не нужен.
[Поиск установленного Python в uv](https://docs.astral.sh/uv/concepts/python-versions/).

## 4. Заполнить .env и проверить VK ID

```bash
cd /opt/vk-volleyball-bot
runuser -u botuser -- cp .env.example .env
chmod 600 .env
nano .env
```

Пример значений — замени токен, ID беседы и ID администратора на свои:

```dotenv
VK_TOKEN=твой_токен_сообщества
CHAT_PEER_ID=2000000001
COLLECT_WEEKDAY=0
COLLECT_TIME=08:00
EVENT_WEEKDAY=1
EVENT_TIME=19:30
REMIND_ENABLED=true
REMIND_WEEKDAY=1
REMIND_TIME=08:00
ADMIN_VK_IDS_RAW=123456789
DATA_PATH=/var/lib/vk-volleyball-bot/participants.json
```

Дни недели: `0` — понедельник, `6` — воскресенье. В примере анонс выходит в
понедельник 08:00, тренировка начинается во вторник 19:30, напоминание — во
вторник 08:00. Начало тренировки автоматически закрывает запись. Эти три момента
не должны совпадать. В nano: `Ctrl+O`, Enter — сохранить; `Ctrl+X` — выйти.

Если ID нужной беседы ещё не известен, сначала сохрани действующий `VK_TOKEN`,
затем запусти:

```bash
runuser -u botuser -- .venv/bin/python -m src.vk_ids chat --timeout 300
```

Дождись сообщения **«Готово»**, отправь **новую уникальную метку, указанную
утилитой**, в нужную беседу и скопируй выведенный `CHAT_PEER_ID` в `.env`.
Утилита работает от имени сообщества: ID из URL личного аккаунта может отличаться.
В момент определения ID основной бот с этим токеном должен быть остановлен
на всех компьютерах/серверах.

Для получения ID администраторов (замени ссылки на реальные профили):

```bash
runuser -u botuser -- .venv/bin/python -m src.vk_ids users \
  'https://vk.com/id123456789' 'https://vk.com/короткое_имя'
```

Перенеси выведенную строку `ADMIN_VK_IDS_RAW=...` в `.env`. Утилита ID не изменяет
файл автоматически. Подробный порядок настройки сообщества, токена, доступа к
переписке и Long Poll — в [README](../README.md#настройка-сообщества-vk).
Для кнопок нужен `message_event`, для текстовых команд — `message_new`.

Для удаления админ-команд выдай сообществу права администратора беседы.
Для личной справки каждый администратор должен открыть диалог с сообществом,
написать ему и разрешить сообщения.

Проверь конфигурацию без подключения бота к VK и без показа токена:

```bash
runuser -u botuser -- .venv/bin/python -c \
  'from src.config import Config; c = Config(); print("Config OK; peer_id:", c.chat_peer_id, "data:", c.data_path)'
```

Если возник `ValidationError`, исправь `.env`. Не публикуй полный вывод ошибки:
он может содержать значения настройки. Файл `.env` принадлежит `botuser`, права
`600` позволяют читать его службе; проверь при необходимости:

```bash
ls -l .env /var/lib/vk-volleyball-bot
```

## 5. Перенести текущие данные, если бот уже работал

Если список нужно сохранить, **до первого запуска в LXC** останови прежнего бота
и перенеси целиком файл, указанный в старом `DATA_PATH`, в:

```text
/var/lib/vk-volleyball-bot/participants.json
```

Сохраняй весь JSON: в нём находятся не только участники, но и состояние события,
время начала и ID карточки. Один файл переносится только в ту же VK-беседу.
После копирования внутри контейнера:

```bash
chown botuser:botuser /var/lib/vk-volleyball-bot/participants.json
chmod 600 /var/lib/vk-volleyball-bot/participants.json
```

Перенести файл можно через доступный SSH/SCP либо через узел Proxmox. Если файл
уже лежит **на узле Proxmox** в `/root/participants.json`, команда на узле:

```bash
pct push 120 /root/participants.json /var/lib/vk-volleyball-bot/participants.json
```

Затем выполни `chown`/`chmod` в контейнере. При новой установке пропусти этот
шаг: файл будет создан после первого сохранения. Пустой файл создавать не нужно.
Локальную копию, запущенную нашими Windows-скриптами, останавливай через
`scripts/stop_bot.ps1`; Linux-копию — через `scripts/stop_bot.sh`.

### Необязательная ручная проверка скриптами запуска

До включения службы можно проверить фоновой запуск скриптом. Команды из папки
проекта выполняют запуск/остановку от того же пользователя, который владеет
данными:

```bash
cd /opt/vk-volleyball-bot
runuser -u botuser -- bash scripts/start_bot.sh
tail -f logs/bot.stderr.log
```

После проверки нажми `Ctrl+C`, чтобы закрыть просмотр логов, затем:

```bash
runuser -u botuser -- bash scripts/stop_bot.sh
```

Теперь переходи к включению systemd. Если служба уже работает, перед ручной
проверкой сначала останови её через `systemctl stop vk-bot`. После остановки
скриптовой копии верни службу командой `systemctl start vk-bot`.

## 6. Включить сервис systemd

Готовый unit находится в [deploy/vk-bot.service](../deploy/vk-bot.service).
Выполни внутри контейнера:

```bash
cat > /etc/systemd/system/vk-bot.service <<'EOF'
[Unit]
Description=VK Volleyball Bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=botuser
Group=botuser
WorkingDirectory=/opt/vk-volleyball-bot
Environment=TZ=Europe/Moscow
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/vk-volleyball-bot/.venv/bin/python -X utf8 -m src.main
Restart=always
RestartSec=10
TimeoutStopSec=30
KillMode=control-group
UMask=0077
NoNewPrivileges=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
systemd-analyze verify /etc/systemd/system/vk-bot.service
systemctl daemon-reload
systemctl enable --now vk-bot.service
systemctl status vk-bot.service --no-pager
journalctl -u vk-bot.service -n 50 --no-pager
```

Unit использует пользователя `botuser`, рабочую папку проекта и `.venv/bin/python`
напрямую: при перезапуске службы установка зависимостей не выполняется. `.env`
читает `Config` из рабочей папки, `EnvironmentFile=` не нужен. После сбоя systemd
перезапускает бота через 10 секунд; ручной `systemctl stop` останавливает службу.
[Поведение systemd в Ubuntu](https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html).

Назначение основных строк unit:

| Строка | Смысл |
|---|---|
| `User` / `Group` | Запуск процесса с правами `botuser`, а не root |
| `WorkingDirectory` | Папка, из которой Python импортирует `src` и `Config` читает `.env` |
| `ExecStart` | Конкретная программа и параметры её запуска |
| `Restart=always`, `RestartSec=10` | Возобновить работу после завершения процесса, с паузой 10 секунд; ручной stop остаётся остановкой |
| `WantedBy=multi-user.target` | Позволить enable подключить службу к обычной загрузке системы |
| `Wants` / `After=network-online.target` | Запрашивать системную готовность сети перед запуском; соединение с VK проверяй по журналу |

Остальные строки уточняют поведение: `TZ` задаёт московский часовой пояс,
`PYTHONUNBUFFERED=1` выводит логи без буферизации, `-X utf8` включает UTF-8
в Python. `TimeoutStopSec=30` даёт время остановиться, `KillMode=control-group`
охватывает дочерние процессы. `UMask=0077` закрывает новые файлы от других
пользователей, `NoNewPrivileges=true` запрещает процессу повышать привилегии.
Вывод направлен в системный журнал. Часть этих строк уточняет значения по
умолчанию; их присутствие делает поведение службы явным.
[Параметры окружения и прав службы](https://manpages.debian.org/trixie/systemd/systemd.exec.5.en.html).

`daemon-reload` перечитывает изменённые файлы служб; обычное изменение `.env`
требует только `restart`. `enable --now` сразу включает автозапуск и запускает
службу. `status` и `journalctl` показывают состояние и ошибки; они не запускают
вторую копию бота.

В статусе ожидается **`active (running)`**, в журнале — `Config loaded`,
`Storage initialized`, `Bot handlers registered` без повторяющихся ошибок.
Для постоянной работы используй именно systemd; не запускай параллельно
`start_bot.sh`, `uv run python -m src.main` или бот на своём ПК.

## 7. Проверить работу и автозапуск

В нужной VK-беседе:

1. Отправь `Админ помощь`: команда должна исчезнуть, справка — прийти лично.
2. Если открытого события нет, создай ручное тестовое событие на ближайшее
   будущее. В контейнере команда `date -d '+10 minutes' '+Создать событие %d.%m.%Y %H:%M'`
   выведет готовый текст для отправки в VK. Время теста должно быть раньше
   следующего еженедельного анонса; иначе бот откажет из-за пересечения.
   Если запись уже открыта, проверь существующую карточку вместо нового теста.
3. Проверь карточку и кнопки «Записаться», «Отписаться», «Помощь».
4. В момент начала карточка должна показывать закрытую запись.
5. После проверки отправь `Удалить событие`, если это был только тест.

Анонс по недельному расписанию ждёт следующего заданного момента; сам запуск
бота не открывает новую запись. При восстановлении просроченного события запись
закрывается с сохранением списка.

Для проверки перезапуска контейнера выполни внутри него:

```bash
reboot
```

Console отключится. Дождись загрузки и снова открой Console. Затем:

```bash
systemctl is-enabled vk-bot.service
systemctl is-active vk-bot.service
journalctl -u vk-bot.service -b -n 30 --no-pager
```

Ожидается `enabled` и `active`. Дополнительно проверь **Start at boot = Yes**
в Proxmox: systemd запускает службу внутри контейнера, а эта опция запускает сам
контейнер после загрузки узла.

## 8. Повседневные команды

Команды от root **внутри контейнера**:

```bash
systemctl start vk-bot.service
systemctl stop vk-bot.service
systemctl restart vk-bot.service
systemctl status vk-bot.service --no-pager
journalctl -u vk-bot.service -f
```

`Ctrl+C` в `journalctl -f` закрывает просмотр журнала, бот продолжает работать.
После редактирования `.env` выполни `systemctl restart vk-bot.service`.
Логи службы находятся в journald; файлы `logs/bot.*.log` относятся к запуску
shell-скриптами и при этом способе не используются.

## 9. Обновление и резервная копия

Перед обновлением останови службу, сохрани конфигурацию и данные:

```bash
systemctl stop vk-bot.service
cd /opt/vk-volleyball-bot
backup_dir="/root/vk-bot-backups/$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$backup_dir"
cp -a .env "$backup_dir/.env"
if [ -f /var/lib/vk-volleyball-bot/participants.json ]; then
  cp -a /var/lib/vk-volleyball-bot/participants.json "$backup_dir/participants.json"
fi
runuser -u botuser -- git rev-parse HEAD >"$backup_dir/revision.txt"

runuser -u botuser -- git pull --ff-only
runuser -u botuser -- uv sync --frozen --no-dev --python 3.12
```

Если обе последние команды успешны:

```bash
if [ -f deploy/vk-bot.service ]; then
  install -m 0644 deploy/vk-bot.service /etc/systemd/system/vk-bot.service
  systemctl daemon-reload
fi
systemctl start vk-bot.service
systemctl status vk-bot.service --no-pager
```

При ошибке обновления/установки зависимостей сначала исправь её; не считай
обновление завершённым. `git pull` не заменяет `.env` и JSON-данные.
Сохраняй резервные копии контейнера также в **Proxmox → Backup** либо через
Proxmox Backup Server. Храни данные внутри root disk, как в этой инструкции:
содержимое bind mount не входит в обычный `vzdump` backup.
[Правила резервирования LXC](https://github.com/proxmox/pve-docs/blob/master/pct.adoc#bind-mount-points).
При восстановлении резервной копии останови исходный экземпляр перед запуском
восстановленного, чтобы два бота не работали с одним токеном.

## 10. Если что-то не работает

| Симптом | Что проверить |
|---|---|
| `status=203/EXEC` | Существует `.venv/bin/python`, Python установлен именно для `botuser`; повтори `uv sync` от его имени |
| `status=200/CHDIR` | Существует `/opt/vk-volleyball-bot`, пользователь имеет доступ к каталогу |
| `PermissionError` при сохранении | Владелец `/var/lib/vk-volleyball-bot` — `botuser`; ему нужны права записи в каталог для временного файла и атомарной замены |
| `ValidationError` | Токен, ID, формат времени, список администраторов и совпадения расписания в `.env` |
| Сервис постоянно перезапускается | `journalctl -u vk-bot.service -n 100 --no-pager`; если достигнут лимит запусков, после исправления `systemctl reset-failed vk-bot.service` и `systemctl start vk-bot.service` |
| `active`, но в VK тишина | Верный `CHAT_PEER_ID`, доступ сообщества ко всей переписке, `message_new`, действующий токен и исходящий HTTPS |
| Кнопки не работают | Включён `message_event` в Long Poll API сообщества |
| Админ-команды остаются | Сообщество имеет права администратора беседы; проверь ошибки удаления в журнале |
| Нет личной админ-справки | Администратор разрешил сообщения от сообщества и указан в `ADMIN_VK_IDS_RAW` |
| Неверное время события | `date` в контейнере, часовой пояс `Europe/Moscow`, часы на узле |
| После перезагрузки Proxmox бот не работает | Контейнер запущен, Start at boot включено, `vk-bot.service` enabled |

Инструкция составлена по коду проекта и официальной документации. На твоём
Proxmox выполнение пока не проверялось.
