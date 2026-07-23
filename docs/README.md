# Индекс документации VK Volleyball Bot

Добро пожаловать в документацию. Здесь подробно описана архитектура, API, конфигурация и деплой бота.

## Структура документации

| Файл | Содержание |
|------|------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Общая архитектура, потоки данных, конкурентность, безопасность |
| [API_REFERENCE.md](API_REFERENCE.md) | Полная документация по каждому модулю `src/` — классы, функции, сигнатуры, сложность |
| [CONFIGURATION.md](CONFIGURATION.md) | Переменные окружения, получение токена VK, настройка планировщика, часовые пояса |
| [BOT_COMMANDS.md](BOT_COMMANDS.md) | Список команд бота с примерами диалогов и сценариями |
| [TESTING.md](TESTING.md) | Проверка работоспособности: автотесты, локальный запуск, ручная проверка в VK, Docker |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Инструкции по деплою: Docker (`docker compose up -d`), systemd, VPS, облачные платформы |
| [CLOUDRU.md](CLOUDRU.md) | Пошаговый деплой на **cloud.ru** через ВМ: VM, Container Registry, Object Storage, security groups |
| [CLOUDRU_CONTAINERAPPS.md](CLOUDRU_CONTAINERAPPS.md) | Serverless-деплой на **cloud.ru** Container Apps: без ВМ, min instances=1, Artifact Registry, S3-том |

## Быстрый старт для разработчика

1. [ARCHITECTURE.md](ARCHITECTURE.md#общая-структура) — поймите, как устроен бот
2. [API_REFERENCE.md](API_REFERENCE.md) — изучите контракты модулей
3. [CONFIGURATION.md](CONFIGURATION.md) — настройте `.env`
4. [TESTING.md](TESTING.md) — проверьте работоспособность
5. [DEPLOYMENT.md](DEPLOYMENT.md#способ-1-docker-рекомендуется) — задеплойте (общие инструкции)
6. [CLOUDRU.md](CLOUDRU.md) — если деплоите на **cloud.ru** через ВМ
7. [CLOUDRU_CONTAINERAPPS.md](CLOUDRU_CONTAINERAPPS.md) — если деплоите на **cloud.ru** Container Apps (без ВМ)

## Быстрый старт для пользователя

1. Добавьте бота в групповой чат VK
2. Напишите `+` чтобы записаться
3. Напишите `+ Имя` чтобы записать друга
4. Напишите `список` чтобы увидеть участников
5. Напишите `?` для справки
