# AnimeVault Cards — Fate Collection

Telegram-бот на Python 3.12, aiogram, SQLAlchemy/asyncpg, PostgreSQL 16+ и Alembic.
Обычный чат и long polling; без Mini App, магазина, доната и реальных платежей.

## Fate Collection — First Summon

| ID | Имя | Класс | Редкость | Шанс |
| --- | --- | --- | --- | --- |
| AV-F001 | Artoria Pendragon | Saber | SSR | 33% |
| AV-F002 | Morgan le Fae | Berserker | SSR | 33% |
| AV-F003 | Enkidu | Lancer | SSR | 33% |
| AV-L001 | Kama & Ishtar | Assassin × Archer | UR Limited | 1% |

Публичный каталог и новые розыгрыши содержат **только эти четыре карточки**.
Kama & Ishtar — один предмет. Шансы хранятся в целых базисных пунктах
3300/3300/3300/100 из 10000; `secrets.randbelow(10000)` выбирает каждый результат
независимо. Нет pity, скрытых множителей, скидки ×10 или гарантий.

Валюта — AnimeVault Coins. Новый аккаунт получает 1 000 монет в одной транзакции
с созданием игрока. ×1 стоит 100, ×10 — 1 000. Повторный /start не начисляет бонус;
существующие балансы сохраняются. Сохранены ежедневная награда 300 монет по UTC,
опыт/уровни, история и избранное. Старые экземпляры доступны в архиве коллекции 0.2
и не учитываются в прогрессе Fate 0/4–4/4. Старые строки БД не удаляются.

## Исходные PNG

Добавить **готовые оригиналы**, без изменения дизайна:

```text
assets/cards/AV-F001.png
assets/cards/AV-F002.png
assets/cards/AV-F003.png
assets/cards/AV-L001.png
```

Все четыре исходных PNG присутствуют в репозитории. Они не заменяются и не
перерисовываются. При потере или повреждении файлов новые призывы безопасно
останавливаются до списания монет. Проверка без DATABASE_URL и BOT_TOKEN:

```sh
uv run python -m cardlegends.cli check-cards
```

PNG отправляются без перерисовки, обрезки и надписей. В пределах ограничений
sendPhoto — изображением; иначе исходным PNG-документом до 50 МБ. Telegram может
сжимать отправленные фотографии; локальный оригинал сохраняется. Кэш file_id
зависит от байтов PNG; при отказе кэша используется файл. Смена оригинала меняет
ключ кэша. Docker и wheel включают assets/cards. Добавлять файлы **до сборки образа**.

## Команды и кнопки

`/start`, `/summon`, `/collection`, `/catalog`, `/profile`, `/rates`, `/help`,
`/history`, `/daily`. Главное меню: призвать ×1/×10, моя коллекция, каталог,
профиль, шансы, история, ежедневная награда, архив.

×1 показывает PNG, имя, класс, редкость, стоимость и баланс операции. ×10 выводит
все десять результатов, число повторов и кнопки просмотра PNG в одном сообщении.
В личной коллекции неполученные карточки закрыты, полученные сгруппированы по коду
с количеством копий. Общий каталог доступен независимо от владения.

Ключ кнопки и Telegram update защищают от повторного списания. Транзакция блокирует
строку игрока, проверяет баланс и сохраняет все результаты атомарно. Ошибка Telegram
после commit не отменяет результат: доступны история и повтор показа. Лимит новых
призывов — один запрос в секунду на игрока, проверяется в БД после проверки повтора
и не меняет шансы. Старые кнопки призыва открывают новый баннер без списания.

## Запуск

```sh
uv sync --frozen --extra dev
# Настроить защищённые переменные окружения; .env приложение само не читает.
uv run python -m cardlegends.cli check-cards
uv run alembic upgrade head
uv run python -m cardlegends.main
```

Бот выполняет идемпотентный seed четырёх карточек до polling, под общей PostgreSQL
transaction advisory lock. Миграции не запускаются из Telegram handlers.

Docker Compose (пароли/DSN заполнить в локальном .env, не коммитить):

```sh
cp .env.example .env
# Добавить исходные PNG и заполнить настройки.
docker compose up --build
```

Локальный migrate выдаёт минимальные runtime-grants существующей cardlegends_app.
Для управляемой БД роли и grants настраивает оператор. Seed нужны SELECT/INSERT
на franchises, characters, banners, banner_pools; после заполнения достаточно SELECT.
Остальные grants: SELECT на image_assets; SELECT/INSERT на summons, summon_results,
currency_transactions, daily_rewards; SELECT/INSERT/UPDATE на players, wallets,
owned_cards, render_cache; CONNECT к БД и USAGE схемы. DDL runtime-роли не нужен.
Возможен отдельный `python -m cardlegends.cli seed` под DML-ролью до старта бота.

## Обновление Dada Cloud

Известная команда остаётся совместимой, если DATABASE_URL использует роль с правами
и мигратора, и seed:

```sh
sh -c 'alembic upgrade head && exec python -m cardlegends.main'
```

С разными ролями выполнить миграцию отдельной задачей, затем seed под DML-ролью
или выдать runtime INSERT на четыре таблицы каталога. Бот запускается с исходным
ограниченным DATABASE_URL. Alembic **не выбирает MIGRATION_DATABASE_URL сам**.
Не менять TLS/DSN ради этой функции. Существующая обработка sslmode сохранена.
Одна polling-реплика, фоновый процесс, без нового порта и HTTP healthcheck.

[Аудит, решения, проверка прав, backup и точная процедура обновления](docs/fate-first-summon.md).
Не удалять БД/volume и не выполнять downgrade production-БД.

## Проверки и обслуживание

```sh
docker compose -f compose.test.yml up -d --wait
uv run ruff check .
uv run ruff format --check .
TEST_DATABASE_URL=postgresql+asyncpg://cardlegends_test:local_test_only@localhost:55432/cardlegends_test uv run pytest -q
uv build --wheel
```

Тесты применяют и откатывают миграции только в отдельной БД с суффиксом `_test`;
не запускать их на production. PNG в тестах — маленькие синтетические fixtures,
они не поставляются как карточки. Реальные Telegram API и Dada Cloud в тестах
не используются. `check-cards` отдельно показывает готовность исходных файлов.

Административные команды seed/list/metadata/manual/approve/import/review/reject
сохранены. Для новых Fate-карт runtime использует только подготовленный PNG,
а не внешние арты; команда render копирует его без изменения. Старые арты и их
права хранятся в БД. Исходные отчёты v0.2 и предыдущие инструкции описывают прошлые
версии: [v0.2](docs/v020-report.md), [первоначальный bootstrap](docs/catalog-bootstrap.md).


## Gameplay UI v2

Профиль со статистикой, фильтры экземпляров, отряд, MAX за EXP, башня с пятью
архетипами и отчётом, дневные/недельные задания, 17 достижений, семидневный
календарь без сброса за пропуск, постоянные квитанции наград, игровая почта и
необязательное обучение доступны через новое главное меню.

Новые команды: `/quests`, `/weekly`, `/achievements`, `/rewards`, `/mail`,
`/calendar`, `/activities`, `/tutorial`. Сохраняются `/team`, `/tower`, `/daily`
и прежние команды Fate. Награды и почта начисляются атомарно и только один раз.

Миграция `0006_gameplay_ui_v2` сохраняет прежние уровни, NP, EXP, монеты,
отряд и историю. Башня использует новый плавный баланс: первая победа даёт
`min(400, 80+25f)` Coins и `min(300, 35+15f)` EXP, повтор — половину.
Уже сохранённые награды не пересчитываются. Шансы Fate и цены призыва/прокачки
не изменены. Перед Railway-обновлением необходим backup и права на новые таблицы.

[Полный аудит, формулы, таблицы наград, сценарии экономики, права и обновление](docs/gameplay-ui-v2.md).
