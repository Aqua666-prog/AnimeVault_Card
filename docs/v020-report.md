# First Playable Release 0.2.0

Исходная main: b6a25fff6bac77185948c6830b4fc73718bc5469. Ветка feature/first-playable-cards-v020. Секреты, TLS и архитектура развёртывания не изменялись; production-деплой, merge и GitHub Release не выполнялись.

## Аудит A

| Функция в исходной main | Состояние | Решение |
|---|---|---|
| players, /start, /profile, /help | Работают | Сохранены регистрация и идентичность |
| SQLAlchemy/Alembic/asyncpg | Работают | Добавлена одна миграция без изменения players |
| Диагностика PR #1/#2 | Работает | Сохранена, db_check доступен |
| Гача/валюта/каталог/коллекция | Отсутствуют | Реализованы |
| Иллюстрации/источники/права | Отсутствуют | Pillow, PostgreSQL-хранилище и административный процесс |
| Telegram меню игровых разделов | Заглушки | В production подключён рабочий game-router |
| Тесты | 105 проверок | Сохранены, расширены; fixtures очищают players CASCADE из-за новых FK |
| CI | Отсутствует | GitHub Actions с PostgreSQL 16 |

## Реализация B–D

30 персонажей, шесть франшиз, все пять редкостей, стабильные ID/slug, JSON aliases/tags/variant. Идемпотентный seed создаёт каталог и постоянный баннер. Каталог отделён от экземпляров. Пулы и параметры баннера сохраняются в PostgreSQL, включая сроки и статус.

Кошелёк, ledger, награда UTC, серверный SystemRandom, одиночный призыв и ×10, явная гарантия SR+. При отсутствии SR+ в десяти слотах последний перевыбирается из условного распределения 15:4:1. Идемпотентность по player/key; блокировка строки players сериализует экономику одного игрока. Списание, XP, ledger, summon/results и owned_cards находятся в одной транзакции. Инициализация кошелька — однократные 1000 кристаллов, в том числе для существующих игроков при первом игровом обращении.

Telegram UX: меню, профиль, каталог/коллекция с пагинацией, фильтрами и сортировкой, избранное с явным idempotent set, история последних десяти призывов, результаты ×10 по страницам, карточки/дубликаты и возвраты. Callback не содержит цены, баланс или результаты; все операции ограничены текущим отправителем. ID профиля доступен только в личном чате. Старые/неизвестные кнопки подтверждаются и ведут в меню. Старый базовый router сохранён для совместимости API тестов, но production main подключает игровой router.

12 новых таблиц: franchises, characters, image_assets, wallets, banners, banner_pools, summons, owned_cards, summon_results, currency_transactions, daily_rewards, render_cache. Миграция 54b3f0a493e1 поверх 0001_players. При upgrade старые данные players не удаляются. Downgrade удаляет только новые игровые таблицы; применять лишь в тесте или при согласованном откате.

Арты: по умолчанию 30 динамически генерируемых нейтральных заглушек. Реальных сторонних артов в поставке **нет**. DejaVu Sans включён с лицензией. Бинарные исходники и права в PostgreSQL, Telegram file_id — только кеш, восстановление из исходников/шаблона. Нет зависимости от платного объектного хранилища и локального тома. Рендер Pillow в потоке с единовременным лимитом; сетевой импорт в запуске бота отсутствует.

Danbooru: metadata-адаптер, проверка канонического тега через tags.json, поиск, фильтры и рейтинг качества, throttle/backoff/429/5xx. Live-поиск не подтверждён: help:api возвращал Downbooru; изучен также публичный posts_controller.rb официального репозитория. Теги в catalog.json — проверяемая конфигурация, а не утверждение об успешной live-валидации.

Zerochan: официальная /api получена с проектным User-Agent, подтверждены read-only GET, лимит 60/min и требование username в User-Agent. Реализован adapter, требующий собственный ZEROCHAN_USERNAME, и ручной импорт разрешённых файлов/страниц. Доступ к API от аккаунта не проверялся: username не предоставлен. Контракты проверены mock-тестами. Правила API не являются лицензией на изображения; все кандидаты остаются unknown_license до проверки оператором.

## Проверки E

Полный финальный прогон: **119 passed, 103.09 s**, без пропусков. `pytest --collect-only` также подтверждает 119 тестов. Предыдущий прогон 117 passed и отдельный регрессионный тест 1 passed заменены полным итоговым запуском. Одна попытка проверки была прервана из-за уже остановленной одноразовой тестовой БД; после восстановления окружения полный запуск прошёл.

Регрессия обновления/перезапуска подтверждает сохранение UUID/created_at существующего игрока при upgrade, баланса, опыта, карточки, избранного и использованной ежедневной награды после нового engine.

Дополнительно повторён игровой/API/графический набор: 12 passed, 36.09 s. Проверены блокировка гонок, стартовая валюта/UTC-награда, один и десять результатов, явная гарантия SR+, отрицательный баланс, повтор callback, инъекция ошибки при вставке карточки с полным rollback, чужие карточки, дубликаты/фильтры/избранное/история, повторный seed/импорт, запрет публикации неподтверждённого арта, mock API/429/SFW, все 30 детерминированных JPEG 750×1050 и реальные Telegram Dispatcher callbacks без сети.

Ruff lint, format --check, git diff --check прошли. Wheel cardlegends-0.2.0 собран; наличие catalog.json, DejaVuSans.ttf и лицензии проверено внутри wheel. Миграции проверены на PostgreSQL 16 (upgrade, downgrade, ORM compare_metadata) в полном прогоне; SQLite не использовался. CI добавлен, но его удалённый результат не приравнивается к локальному до отдельного подтверждения.

## Развёртывание F

1. Пересобрать Dockerfile из PR после его утверждения/слияния.
2. Существующие секреты остаются в Dada Cloud; не менять DATABASE_URL и sslmode=disable автоматически. Безопасность выбранного режима подтвердить у провайдера отдельно.
3. Сохранить штатную команду `sh -c 'alembic upgrade head && exec python -m cardlegends.main'`.
4. После миграций один раз `python -m cardlegends.cli seed` с правами владельца схемы, затем при необходимости перезапустить бот. Runtime-роль должна получить новые минимальные grants (список в README/scripts/migrate.py).
5. Одна реплика, без HTTP endpoint/volume. Реальные ограничения 250m CPU / 256 MiB на Dada Cloud не измерены; ограничения изображений/одного рендера предусмотрены, необходим ручной нагрузочный smoke.
6. Полный сценарий ручной проверки — README. Новый Docker-образ и настоящий Telegram API здесь не запускались; wheel и наличие ресурсов проверены.

Ограничения: нет встроенной автоматической проверки лицензий/визуальной модерации — утверждение делает оператор; нет автоматического скачивания сторонних артов; Zerochan API не проверен с настоящим аккаунтом; Danbooru недоступен для live-проверки. Список коллекции показывает экземпляры, а число дубликатов — в открытой карточке. История ограничена последними десятью операциями интерфейса, записи в БД не удаляются. Активный баннер в интерфейсе один; будущие баннеры требуют отдельного UX. Локализация — русский; названия аниме и имена собственные сохраняются.

Изменённые файлы и ссылка PR приведены в финальном отчёте и доступны в Git diff этого PR.

## Изменённые файлы

- `.env.example`
- `.github/workflows/tests.yml`
- `README.md`
- `alembic/versions/54b3f0a493e1_first_playable_catalog_economy_and_.py`
- `docs/architecture.md`
- `docs/dada-cloud.md`
- `docs/v020-placeholder.jpg`
- `docs/v020-report.md`
- `pyproject.toml`
- `scripts/migrate.py`
- `src/cardlegends/application/game.py`
- `src/cardlegends/assets/DejaVuSans.ttf`
- `src/cardlegends/assets/FONT-LICENSE.txt`
- `src/cardlegends/assets/catalog.json`
- `src/cardlegends/bot/app.py`
- `src/cardlegends/bot/game_ui.py`
- `src/cardlegends/cli.py`
- `src/cardlegends/domain/game.py`
- `src/cardlegends/infrastructure/art_admin.py`
- `src/cardlegends/infrastructure/art_sources.py`
- `src/cardlegends/infrastructure/cards.py`
- `src/cardlegends/infrastructure/catalog.py`
- `src/cardlegends/infrastructure/game_models.py`
- `src/cardlegends/infrastructure/game_repository.py`
- `src/cardlegends/infrastructure/models.py`
- `src/cardlegends/main.py`
- `tests/conftest.py`
- `tests/integration/conftest.py`
- `tests/integration/test_game.py`
- `tests/integration/test_startup.py`
- `tests/unit/test_cards_release.py`
- `tests/unit/test_lifecycle.py`
- `uv.lock`
