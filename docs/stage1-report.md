# Отчёт: CardLegends, этап 1

Дата: 2026-11-05. Проект: `/workspace/cardlegends`. Ветка: `feat/foundation-stage-1`.

## Проверка исходного состояния

В `/workspace` не было Git-репозитория, README, конфигураций, тестов или AGENTS.md. Существовавшие служебные каталоги `library-files`, `scratch`, `shared` сохранены. Проект создан в отдельном каталоге `cardlegends`; Git инициализирован с именем основной ветки main, работа выполнена на новой ветке feat/foundation-stage-1. Исходной истории, удалённого репозитория и актуального remote main не было, поэтому ответвиться от существующего коммита основной ветки невозможно. Пользовательские файлы не перезаписывались.

## Реализовано

- Python 3.12+, aiogram 3, SQLAlchemy async, asyncpg, Alembic, Pydantic Settings; зависимости зафиксированы в uv.lock.
- Модульный монолит: Telegram-адаптер → сервисы/DTO → доменные контракты → PostgreSQL-репозиторий. DI через middleware, SQL отсутствует в handlers.
- players с UUID, уникальным BIGINT telegram_id, публичными полями и временными метками TIMESTAMPTZ. Миграция 0001_players с downgrade.
- Атомарная регистрация через PostgreSQL upsert: повторный /start обновляет поля, сохраняет UUID и дату регистрации; конкурентные вызовы не создают дублей.
- /start, /profile, /help и меню из пяти кнопок. Личный профиль содержит ID, имя, дату регистрации UTC и статус «Новичок». Чужой профиль запросить нельзя.
- Уведомления «Раздел в разработке», обработка неизвестных команд и callback, безопасное HTML-экранирование.
- Безопасная диагностика конфигурации и ошибок БД, JSON-логи с update correlation ID, ограниченные таймауты БД, асинхронное закрытие ресурсов.
- Dockerfile с непривилегированным пользователем, Compose с PostgreSQL 16-alpine, отдельным мигратором и ограниченной ролью бота. Данные приложения сохраняются в volume; тестовые — в tmpfs.
- README с BotFather, настройкой, запуском, миграциями, тестами, командами и ограничениями; отдельная архитектурная документация с диаграммой и проектными границами следующих этапов.

## Дерево и перечень изменённых файлов

Все перечисленные файлы новые; существующих файлов проекта не было. `.env`, виртуальное окружение, кэши и временные секреты в Git не добавлены.

```text
cardlegends/
├── alembic/
│   ├── versions/
│   │   └── 0001_players.py
│   ├── env.py
│   └── script.py.mako
├── docs/
│   ├── architecture.md
│   └── stage1-report.md
├── scripts/
│   ├── init-db.sh
│   └── migrate.py
├── src/
│   └── cardlegends/
│       ├── application/
│       │   ├── __init__.py
│       │   └── players.py
│       ├── bot/
│       │   ├── __init__.py
│       │   ├── app.py
│       │   ├── handlers.py
│       │   ├── keyboards.py
│       │   └── middleware.py
│       ├── domain/
│       │   ├── __init__.py
│       │   └── players.py
│       ├── infrastructure/
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── database.py
│       │   ├── logging.py
│       │   ├── models.py
│       │   └── players.py
│       ├── __init__.py
│       └── main.py
├── tests/
│   ├── integration/
│   │   ├── conftest.py
│   │   └── test_postgres.py
│   ├── unit/
│   │   ├── test_bot.py
│   │   ├── test_config.py
│   │   ├── test_lifecycle.py
│   │   └── test_services.py
│   └── conftest.py
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── README.md
├── alembic.ini
├── compose.test.yml
├── docker-compose.yml
├── pyproject.toml
└── uv.lock
```

## Локальный запуск

```bash
cd /workspace/cardlegends
cp .env.example .env
# Заполните токен, пароли и согласованные DSN в .env.
docker compose up --build
```

Миграции и выдача прав выполняются автоматически сервисом migrate до запуска bot. Повторный запуск мигратора: `docker compose run --rm migrate`. Данные переживают `docker compose restart bot` и `docker compose down`, пока не удалён volume.

Команды проверок:

```bash
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -m 'not integration' -q
docker compose -f compose.test.yml -p cardlegends-stage1-test up -d --wait
TEST_DATABASE_URL='postgresql+asyncpg://cardlegends_test:local_test_only@127.0.0.1:55432/cardlegends_test' uv run pytest -q
docker compose -f compose.test.yml -p cardlegends-stage1-test down
```

## Проверки и результаты

| Проверка | Фактический результат |
| --- | --- |
| Ruff lint и format --check | Успех, код 0 |
| Модульные тесты без интеграции | 31 passed, 5 deselected; 2.76 s |
| Полный pytest с TEST_DATABASE_URL | **36 passed**, без пропусков; 5.87 s |
| Интеграционная БД | PostgreSQL **16.15**, официальный postgres:16-alpine, отдельная cardlegends_test |
| Повторный и конкурентный upsert | Один игрок, неизменные UUID/created_at; 30 одновременных вызовов |
| Миграции | Upgrade на чистой БД и downgrade после каждого из 5 интеграционных тестов; отсутствие players после отката подтверждено SQL-запросом |
| ORM и миграция | Alembic compare_metadata не обнаружил различий |
| Docker build | Успех, код 0; BuildKit с временным trusted CA secret |
| Compose | Проверка конфигурации — код 0; PostgreSQL healthy, migrate завершился с кодом 0 |
| Повторный запуск контейнера migrate | Успех, код 0; повторный upgrade и GRANT идемпотентны |
| Проверка из образа приложения | UID 10001; успешный вход как cardlegends_app; upsert и чтение через новый engine; роль не superuser; DELETE, CREATE TABLE и чтение alembic_version отклонены |
| Жизненный цикл | Тесты закрытия engine/session при нормальном завершении и сбоях БД, Telegram-команд и polling проходят |

Проверка Compose выполнялась на отдельном одноразовом проекте с автоматически сгенерированными паролями вне репозитория. Для неё использовался уже собранный образ cardlegends:stage1; настоящий Telegram-токен не применялся. После проверок одноразовые контейнеры, сети и smoke-volume удалены. Публичные тестовые учётные данные относятся только к compose.test.yml.

Первый прогон обнаружил необработанный ArgumentError при неверном DSN и потерю понятного сообщения запуска при логировании модуля как __main__; обе ошибки исправлены. Реальный запуск контейнеров выявил закрытые права файлов при umask окружения: исправлены права init-db.sh и чтение файлов приложения непривилегированным пользователем в Dockerfile. Ограничение диска с Docker VFS устранено очисткой созданных в этой задаче контейнеров/образов/кэша и выбором более компактного PostgreSQL 16-alpine. Рабочие пользовательские БД не использовались.

## Ограничения

- Настоящий токен не предоставлен: сетевой запуск polling и ручной сценарий в Telegram не выполнены. Команды/callback проверены через реальный Dispatcher и сессию-перехватчик без сети. После настройки токена требуется ручной smoke: повторный /start, профиль, все кнопки, неизвестная команда и профиль после перезапуска.
- Карточки, гача, валюты, бои, обмены, платежи и webhook не реализованы; этап 2 не начат.
- Нет production-развёртывания, внешнего мониторинга и гарантий exactly-once отправки Telegram-сообщений. В логах намеренно не сохраняются подробные traceback сторонних библиотек.
- Версии Python/PostgreSQL закреплены тегами, зависимости Python — lock-файлом. Теги базовых образов могут обновляться.
- Git remote отсутствует. Создан локальный исходный коммит; точный идентификатор приведён в финальном ответе и доступен через `git log -1 --oneline`. Push, PR, merge и релиз не выполнялись.

## План этапа 2: «Каталог карточек и коллекция»

Только после отдельного разрешения:

1. Согласовать поля шаблона, редкости, характеристики, изображения и правила появления экземпляров; определить критерии готовности.
2. Добавить отдельные card_templates и player_cards с внешними ключами, индексами, политикой версий и обратимой миграцией.
3. Реализовать сценарии каталога и личной коллекции в application и запросы в infrastructure.
4. Добавить русские Telegram-сообщения, пагинацию и пустое состояние коллекции; проверять владение на каждом действии.
5. Проверить миграции, изоляцию коллекций разных игроков, пагинацию, сохранность данных и регрессии этапа 1. Призыв и экономика требуют отдельного согласования.
