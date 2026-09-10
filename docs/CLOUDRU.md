# Деплой на cloud.ru

Руководство по развёртыванию VK Volleyball Bot на платформе [cloud.ru](https://cloud.ru).

---

## Обзор

Для бота достаточно одной виртуальной машины (Cloud Server) с Docker. cloud.ru предоставляет всё необходимое: вычислительные мощности, сетевую изоляцию и S3-совместимое Object Storage для бэкапов.

**Рекомендуемый способ:** Docker Compose на VM.

> Образ запускается не от `root`, поэтому volume `/app/data` должен быть writable для `botuser`. В compose-конфигурации ниже также зафиксированы `mem_limit: 256m`, `cpus: '0.5'` и healthcheck `test -w /app/data`.

---

## Подготовка инфраструктуры

### 1. Создание виртуальной машины

Панель управления → **Compute Cloud** → **Создать ВМ**.

| Параметр | Рекомендация |
|----------|-------------|
| **ОС** | Ubuntu 24.04 LTS (или 22.04 LTS) |
| **Конфигурация** | 1 vCPU, 2 ГБ RAM, 10 ГБ диска — минимально достаточно |
| **Сеть** | Новая или существующая VPC |
| **Публичный IP** | Нужен для прямого SSH; для VK API достаточно исходящего доступа через NAT |
| **SSH-ключ** | Добавьте ваш публичный ключ |

> Боту не нужен входящий доступ из интернета, кроме SSH. VK API исходящий — через NAT по умолчанию.

### 2. Настройка групп безопасности

Панель управления → **VPC** → **Группы безопасности** → привяжите к ВМ:

| Направление | Протокол | Порт | Источник/Назначение | Комментарий |
|-------------|----------|------|---------------------|-------------|
| **Входящий** | TCP | 22 | Ваш IP / подсеть офиса | SSH-администрирование |
| **Исходящий** | Любой | Любой | 0.0.0.0/0 | Доступ к VK API, Docker Hub, NTP |

> Удалите правило "Разрешить всё входящее" (All inbound), если оно есть по умолчанию.

---

## Способ 1: Docker Compose (рекомендуется)

### Шаг 1: Подключение к ВМ

```bash
ssh ubuntu@<публичный_IP_ВМ>
```

### Шаг 2: Установка Docker

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo usermod -aG docker $USER
newgrp docker
```

### Шаг 3: Клонирование репозитория

```bash
cd ~
git clone <URL_репозитория> vk-bot
cd vk-bot
```

### Шаг 4: Настройка окружения

```bash
cp .env.example .env
nano .env
```

Обязательно задайте:

```dotenv
VK_TOKEN=vk1.a.xxx...
CHAT_PEER_ID=2000000001
COLLECT_WEEKDAY=2
COLLECT_TIME=10:00
DATA_PATH=/app/data/participants.json
```

Для локального запуска вне Docker можно оставить `DATA_PATH=data.json`, но в контейнере путь должен быть именно `/app/data/participants.json` и совпадать с volume.

### Шаг 5: Запуск

```bash
docker compose up -d
```

Проверка:

```bash
docker compose logs -f
```

### Шаг 6: Обновление

```bash
cd ~/vk-bot
git pull
docker compose down
docker compose up -d --build
```

---

## Способ 2: cloud.ru Artifact Registry

Если хотите централизованно хранить образ и разворачивать на нескольких ВМ:

### 1. Создание реестра и учётных данных

Создайте Docker-репозиторий в **Artifact Registry**. В панели реестра скопируйте
точный URI образа и команду входа: endpoint и формат имени зависят от проекта.
Создайте сервисный аккаунт с правами на push/pull и сохраните выданные данные.

### 3. Локальная сборка и пуш

На вашей рабочей машине:

```bash
# Выполните команду входа из панели Artifact Registry
docker login <registry-host> -u <registry-user>

# Используйте полный URI, скопированный из реестра
IMAGE_URI=<registry-host>/<project>/<repository>/vk-volleyball-bot:latest
docker build -t "$IMAGE_URI" .

# Пуш
docker push "$IMAGE_URI"
```

### 4. Запуск на ВМ из реестра

На сервере cloud.ru:

```bash
# Логин
docker login <registry-host> -u <registry-user>

# Создаём compose-файл без build
mkdir ~/vk-bot && cd ~/vk-bot
cat > compose.yml << 'EOF'
services:
  vk-bot:
    image: <полный-URI-образа-из-Artifact-Registry>
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
EOF

# .env файл
cp /путь/к/.env .

# Запуск
docker compose -f compose.yml up -d
```

---

## Способ 3: systemd (без Docker)

Если предпочитаете нативный запуск:

```bash
# Системный пользователь и каталог
sudo useradd --system --create-home --home-dir /home/botuser \
  --shell /usr/sbin/nologin botuser
sudo mkdir -p /opt/vk-volleyball-bot
sudo chown botuser:botuser /opt/vk-volleyball-bot

# Системная установка uv без изменения shell-профилей
curl -LsSf https://astral.sh/uv/install.sh \
  | sudo env UV_UNMANAGED_INSTALL=/usr/local/bin sh

# Клонирование
sudo -u botuser git clone <URL> /opt/vk-volleyball-bot
cd /opt/vk-volleyball-bot
sudo -u botuser /usr/local/bin/uv sync --frozen --no-dev
sudo -u botuser cp .env.example .env
# Отредактируйте /opt/vk-volleyball-bot/.env

# systemd
sudo tee /etc/systemd/system/vk-bot.service > /dev/null << 'EOF'
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
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now vk-bot
sudo journalctl -u vk-bot -f
```

---

## Резервное копирование в Object Storage

cloud.ru Object Storage S3-совместим. Рекомендуется бэкапить `data.json`.

### 1. Создание бакета

Панель управления → **Object Storage** → **Создать бакет**.

- Имя: `vk-bot-backups-<уникальный_id>`
- Класс хранения: Стандарт
- Доступ: Приватный

### 2. Создание сервисного аккаунта

Роль: `object-storage.editor`.

Сохраните `Access Key ID` и `Secret Key`.

### 3. Установка aws-cli

```bash
sudo apt-get install -y awscli
```

### 4. Настройка

```bash
aws configure
# AWS Access Key ID: <ключ>
# AWS Secret Access Key: <секрет>
# Default region name: ru-moscow
# Default output format: json
```

### 5. Скрипт бэкапа

Имя Docker volume зависит от имени Compose-проекта. Сначала получите его:

```bash
docker volume ls
```

Подставьте фактическое имя в `VOLUME`:

```bash
sudo tee /usr/local/bin/vk-bot-backup.sh > /dev/null << 'EOF'
#!/bin/bash
BUCKET="vk-bot-backups-<уникальный_id>"
VOLUME="<имя-volume-из-docker-volume-ls>"
DATE=$(date +%Y%m%d-%H%M%S)

if docker run --rm -v "$VOLUME:/data:ro" busybox \
    test -f /data/participants.json; then
    docker run --rm -v "$VOLUME:/data:ro" busybox \
      cat /data/participants.json \
      | aws s3 cp - "s3://$BUCKET/backups/participants-$DATE.json" --endpoint-url=https://s3.cloud.ru
    # Храним последние 7 бэкапов
    aws s3 ls "s3://$BUCKET/backups/" --endpoint-url=https://s3.cloud.ru | sort | head -n -7 | awk '{print $4}' | xargs -I {} aws s3 rm "s3://$BUCKET/backups/{}" --endpoint-url=https://s3.cloud.ru
fi
EOF

sudo chmod +x /usr/local/bin/vk-bot-backup.sh
```

> При systemd-подходе путь к файлу будет другим (указанный в `DATA_PATH`).

### 6. Cron

```bash
sudo crontab -e
```

Добавьте:

```cron
# Бэкап данных каждый день в 03:00
0 3 * * * /usr/local/bin/vk-bot-backup.sh >> /var/log/vk-bot-backup.log 2>&1
```

---

## Мониторинг

### Логи Docker

```bash
docker compose logs -f --tail=100
```

### Логи systemd

```bash
sudo journalctl -u vk-bot -f --since "1 hour ago"
```

### Проверка состояния

```bash
# Docker
docker compose ps
docker stats vk-volleyball-bot --no-stream

# systemd
sudo systemctl status vk-bot
```

### Проверка работоспособности

HTTP endpoint у бота отсутствует. Встроенный Docker healthcheck проверяет только
запись в `/app/data`; подключение к VK контролируйте по логам и тестовой команде
в чате.

---

## Типичные проблемы

### Бот не отвечает после старта

```bash
# Проверьте токен
VK_TOKEN=vk1.a.xxx... docker compose logs

# Проверьте права токена: должен иметь messages + manage
```

### Scheduler не срабатывает

```bash
# Проверьте часовой пояс на ВМ
date
timedatectl status

# Должно быть Europe/Moscow (или ваш TZ)
```

### Данные пропали после пересоздания контейнера

Убедитесь, что используется **именованный volume**, а не bind mount в `/tmp`, и что `DATA_PATH` указывает на файл внутри `/app/data`:

```yaml
volumes:
  - vk-bot-data:/app/data   # правильно
```

### Нет доступа к VK API

Проверьте исходящее соединение:

```bash
curl -I https://api.vk.com
```

Если не работает — проверьте группы безопасности (должно быть разрешено исходящее) и NAT в VPC.

---

## Стоимость

Стоимость зависит от региона, конфигурации ВМ, публичного IP, объёма Object
Storage и трафика. Рассчитайте текущую сумму в
[калькуляторе cloud.ru](https://cloud.ru/pricing) перед созданием ресурсов.

---

## Чек-лист запуска

- [ ] Создана ВМ с Ubuntu 24.04 и исходящим доступом в интернет; при прямом SSH назначен публичный IP
- [ ] Настроена группа безопасности (SSH inbound, любой outbound)
- [ ] Установлен Docker и Docker Compose
- [ ] Volume `/app/data` writable для `botuser`
- [ ] В compose заданы `mem_limit=256m`, `cpus=0.5` и healthcheck
- [ ] Создан `.env` с валидными `VK_TOKEN` и `CHAT_PEER_ID`
- [ ] Бот запущен через `docker compose up -d`
- [ ] Логи проверены, бот отвечает в чате
- [ ] (Опционально) Настроен бэкап в Object Storage
- [ ] Проверены Docker healthcheck, логи и контрольная команда в VK-чате
