import asyncio
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from cardlegends.domain.fate import BANNER_ID, CARD_IDS, DROP_RATES, draw_fate
from cardlegends.domain.game import RARITIES, Economy, GameError
from cardlegends.domain.progression import MAX_NP, upgrade_cost
from cardlegends.infrastructure.fate_images import require_card_images
from cardlegends.infrastructure.game_models import (
    Banner,
    BannerPool,
    CardUpgrade,
    Character,
    CurrencyTransaction,
    DailyReward,
    OwnedCard,
    Summon,
    SummonResult,
    Wallet,
)
from cardlegends.infrastructure.models import PlayerRow


class GameService:
    def __init__(self, sessions, economy=None, randbelow=None, cooldown_seconds=1, cards_dir=None):
        self.sessions, self.economy = sessions, economy or Economy()
        # One original in flight at a time on the small Dada Cloud worker.
        self.media_lock = asyncio.Semaphore(1)
        # Do not share the delivery lock with checks inside a DB transaction:
        # delivery may itself need a connection for the file_id cache.
        self.asset_check_lock = asyncio.Semaphore(1)
        self.randbelow, self.cooldown_seconds, self.cards_dir = (
            randbelow,
            cooldown_seconds,
            cards_dir,
        )

    async def wallet(self, session, telegram_id):
        # Lock player first: all economic operations use the same lock order.
        player = await session.scalar(
            select(PlayerRow).where(PlayerRow.telegram_id == telegram_id).with_for_update()
        )
        if not player:
            raise GameError("Сначала нажмите /start.")
        # Old accounts with no wallet do not receive a new-account bonus via /start.
        # New registrations create their wallet and bonus in the registration transaction.
        await session.execute(
            insert(Wallet).values(player_id=player.id, balance=0, xp=0).on_conflict_do_nothing()
        )
        player.last_seen_at = datetime.now(UTC)
        wallet = await session.get(Wallet, player.id)
        return player, wallet

    async def profile(self, user):
        async with self.sessions.begin() as session:
            player, wallet = await self.wallet(session, user)
            total, unique = (
                await session.execute(
                    select(
                        func.count(OwnedCard.id), func.count(func.distinct(OwnedCard.character_id))
                    )
                    .join(Character, Character.id == OwnedCard.character_id)
                    .where(
                        OwnedCard.player_id == player.id,
                        OwnedCard.consumed_at.is_(None),
                        Character.code.in_(CARD_IDS),
                    )
                )
            ).one()
            summons = await session.scalar(
                select(func.count()).select_from(Summon).where(Summon.player_id == player.id)
            )
            size = len(CARD_IDS)
            archived = await session.scalar(
                select(func.count())
                .select_from(OwnedCard)
                .join(Character, Character.id == OwnedCard.character_id)
                .where(
                    OwnedCard.player_id == player.id,
                    OwnedCard.consumed_at.is_(None),
                    or_(Character.code.is_(None), Character.code.not_in(CARD_IDS)),
                )
            )
            return dict(
                player=player,
                balance=wallet.balance,
                xp=wallet.xp,
                level=1 + wallet.xp // self.economy.xp_per_level,
                total=total,
                unique=unique,
                summons=summons,
                size=size,
                archived=archived,
            )

    async def daily(self, user):
        async with self.sessions.begin() as session:
            player, wallet = await self.wallet(session, user)
            # Explicit UTC calendar day, independent of the session timezone.
            day = await session.scalar(select(func.date(func.timezone("UTC", func.now()))))
            if await session.get(DailyReward, (player.id, day)):
                raise GameError("Сегодня награда уже получена. Возвращайтесь завтра по UTC.")
            wallet.balance += self.economy.daily
            transaction = CurrencyTransaction(
                player_id=player.id,
                kind="daily",
                delta=self.economy.daily,
                balance_after=wallet.balance,
                key=f"daily:{day}",
            )
            session.add(transaction)
            await session.flush()
            session.add(DailyReward(player_id=player.id, day=day, transaction_id=transaction.id))
            return wallet.balance

    async def summon(self, user, count, key, telegram_update_id=None):
        if type(count) is not int or count not in (1, 10):
            raise GameError("Неверный призыв.")
        try:
            key = UUID(key).hex
        except (ValueError, AttributeError, TypeError):
            raise GameError("Кнопка устарела. Откройте /summon.") from None
        if telegram_update_id is not None and (
            type(telegram_update_id) is not int or not 0 <= telegram_update_id < 2**63
        ):
            raise GameError("Некорректный update.")
        async with self.sessions.begin() as session:
            player, wallet = await self.wallet(session, user)
            identity = Summon.key == key
            if telegram_update_id is not None:
                identity = or_(identity, Summon.telegram_update_id == telegram_update_id)
            old = await session.scalar(
                select(Summon).where(Summon.player_id == player.id, identity)
            )
            if old:
                if old.count != count or old.banner_id != BANNER_ID:
                    raise GameError("Эта операция уже выполнена. Откройте историю.")
                return old.id  # Before balance, cooldown, asset and availability checks.
            banner = await session.get(Banner, BANNER_ID)
            now = await session.scalar(select(func.clock_timestamp()))
            if (
                not banner
                or not banner.active
                or banner.drop_rates != DROP_RATES
                or banner.single_cost != 100
                or banner.ten_cost != 1000
                or banner.guaranteed_sr
                or (banner.starts_at and now < banner.starts_at)
                or (banner.ends_at and now >= banner.ends_at)
            ):
                raise GameError("Fate-баннер недоступен. Попробуйте позже.")
            characters = list(
                await session.scalars(
                    select(Character)
                    .join(BannerPool, BannerPool.character_id == Character.id)
                    .where(BannerPool.banner_id == BANNER_ID)
                )
            )
            by_code = {c.code: c for c in characters}
            if (
                set(by_code) != set(DROP_RATES)
                or len(characters) != 4
                or any(not c.is_active or c.id != CARD_IDS[c.code] for c in characters)
            ):
                raise GameError("Каталог баннера недоступен. Обратитесь к администратору.")
            cost = banner.single_cost if count == 1 else banner.ten_cost
            if wallet.balance < cost:
                raise GameError("Недостаточно AnimeVault Coins. Получите ежедневную награду.")
            last = await session.scalar(
                select(func.max(Summon.created_at)).where(Summon.player_id == player.id)
            )
            if last and (now - last).total_seconds() < self.cooldown_seconds:
                raise GameError("Слишком быстро. Подождите секунду и повторите нажатие.")
            async with self.asset_check_lock:
                await asyncio.to_thread(require_card_images, self.cards_dir)
            chosen = draw_fate(count, banner.drop_rates, self.randbelow)
            wallet.balance -= cost
            wallet.xp += count * self.economy.xp_per_card
            summon = Summon(
                player_id=player.id,
                banner_id=BANNER_ID,
                key=key,
                count=count,
                total_cost=cost,
                balance_after=wallet.balance,
                telegram_update_id=telegram_update_id,
                created_at=now,
            )
            session.add(summon)
            await session.flush()
            session.add(
                CurrencyTransaction(
                    player_id=player.id,
                    kind="summon",
                    delta=-cost,
                    balance_after=wallet.balance,
                    key=f"summon:{key}",
                    related_id=str(summon.id),
                )
            )
            for position, code in enumerate(chosen):
                card = OwnedCard(player_id=player.id, character_id=by_code[code].id)
                session.add(card)
                await session.flush()
                session.add(SummonResult(summon_id=summon.id, position=position, card_id=card.id))
            return summon.id

    async def operation(self, user, summon_id):
        async with self.sessions() as session:
            operation = await session.scalar(
                select(Summon)
                .join(PlayerRow)
                .where(PlayerRow.telegram_id == user, Summon.id == UUID(str(summon_id)))
            )
            if not operation:
                raise GameError("Призыв не найден.")
            return operation

    async def collection(self, user):
        async with self.sessions() as session:
            counts = (
                select(
                    OwnedCard.character_id.label("character_id"),
                    func.count().label("quantity"),
                    func.min(OwnedCard.created_at).label("first_obtained"),
                    func.max(OwnedCard.created_at).label("last_obtained"),
                )
                .join(PlayerRow, PlayerRow.id == OwnedCard.player_id)
                .where(PlayerRow.telegram_id == user, OwnedCard.consumed_at.is_(None))
                .group_by(OwnedCard.character_id)
                .subquery()
            )
            return list(
                (
                    await session.execute(
                        select(
                            Character,
                            func.coalesce(counts.c.quantity, 0),
                            counts.c.first_obtained,
                            counts.c.last_obtained,
                        )
                        .outerjoin(counts, counts.c.character_id == Character.id)
                        .where(Character.code.in_(CARD_IDS))
                        .order_by(Character.id)
                    )
                ).all()
            )

    async def fate_character(self, code):
        if code not in CARD_IDS:
            raise GameError("Неизвестный номер карточки.")
        async with self.sessions() as session:
            character = await session.scalar(select(Character).where(Character.code == code))
            if not character:
                raise GameError("Каталог ещё не инициализирован.")
            return character

    async def results(self, user, summon_id):
        async with self.sessions() as session:
            return list(
                (
                    await session.execute(
                        select(OwnedCard, Character)
                        .join(Character, Character.id == OwnedCard.character_id)
                        .join(SummonResult, SummonResult.card_id == OwnedCard.id)
                        .join(Summon, Summon.id == SummonResult.summon_id)
                        .join(PlayerRow, PlayerRow.id == Summon.player_id)
                        .where(PlayerRow.telegram_id == user, Summon.id == UUID(str(summon_id)))
                        .order_by(SummonResult.position)
                    )
                ).all()
            )

    async def browse(
        self,
        user,
        owned=False,
        page=0,
        rarity="all",
        franchise="all",
        sort="rarity",
        fav=False,
        archived=False,
    ):
        async with self.sessions() as session:
            if owned:
                stmt = (
                    select(OwnedCard, Character)
                    .join(Character)
                    .join(PlayerRow, PlayerRow.id == OwnedCard.player_id)
                    .where(PlayerRow.telegram_id == user, OwnedCard.consumed_at.is_(None))
                )
                if fav:
                    stmt = stmt.where(OwnedCard.favorite.is_(True))
            else:
                stmt = select(Character).where(Character.is_active.is_(True))
                if fav:
                    favorites = (
                        select(OwnedCard.character_id)
                        .join(PlayerRow, PlayerRow.id == OwnedCard.player_id)
                        .where(PlayerRow.telegram_id == user, OwnedCard.favorite.is_(True))
                    )
                    stmt = stmt.where(Character.id.in_(favorites))
            if archived:
                stmt = stmt.where(or_(Character.code.is_(None), Character.code.not_in(CARD_IDS)))
            else:
                stmt = stmt.where(Character.code.in_(CARD_IDS))
            if rarity != "all":
                stmt = stmt.where(Character.rarity == rarity)
            if franchise != "all":
                stmt = stmt.where(Character.franchise_id == franchise)
            rank = case({r: i for i, r in enumerate(RARITIES)}, value=Character.rarity)
            stmt = (
                stmt.order_by(rank.desc(), Character.id)
                if sort == "rarity"
                else (
                    stmt.order_by(OwnedCard.created_at.desc(), OwnedCard.id)
                    if owned
                    else stmt.order_by(Character.created_at.desc(), Character.id.desc())
                )
            )
            total = await session.scalar(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
            page = max(0, min(page, max(0, (total - 1) // 5)))
            rows = (await session.execute(stmt.offset(page * 5).limit(5))).all()
            return rows, total, page

    async def card(self, user, card_id):
        async with self.sessions() as session:
            result = (
                await session.execute(
                    select(OwnedCard, Character)
                    .join(Character)
                    .join(PlayerRow, PlayerRow.id == OwnedCard.player_id)
                    .where(
                        PlayerRow.telegram_id == user,
                        OwnedCard.id == UUID(str(card_id)),
                        OwnedCard.consumed_at.is_(None),
                    )
                )
            ).first()
            if not result:
                raise GameError("Карточка не найдена.")
            copies = await session.scalar(
                select(func.count())
                .select_from(OwnedCard)
                .where(
                    OwnedCard.player_id == result[0].player_id,
                    OwnedCard.character_id == result[1].id,
                    OwnedCard.consumed_at.is_(None),
                )
            )
            return *result, copies

    async def owned_character(self, user, code):
        if code not in CARD_IDS:
            raise GameError("Неизвестный номер карточки.")
        async with self.sessions() as session:
            card_id = await session.scalar(
                select(OwnedCard.id)
                .join(Character)
                .join(PlayerRow, PlayerRow.id == OwnedCard.player_id)
                .where(
                    PlayerRow.telegram_id == user,
                    Character.code == code,
                    OwnedCard.consumed_at.is_(None),
                )
                .order_by(
                    OwnedCard.np.desc(),
                    OwnedCard.level.desc(),
                    OwnedCard.created_at,
                    OwnedCard.id,
                )
                .limit(1)
            )
        if card_id is None:
            raise GameError("🔒 Карточка ещё не получена. Полное изображение скрыто.")
        return await self.card(user, card_id)

    async def copies(self, user, code, page=0):
        if code not in CARD_IDS or type(page) is not int or page < 0:
            raise GameError("Карточка не найдена.")
        async with self.sessions() as session:
            stmt = (
                select(OwnedCard)
                .join(Character, Character.id == OwnedCard.character_id)
                .join(PlayerRow, PlayerRow.id == OwnedCard.player_id)
                .where(
                    PlayerRow.telegram_id == user,
                    Character.code == code,
                    OwnedCard.consumed_at.is_(None),
                )
            )
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            page = min(page, max(0, (total - 1) // 8))
            cards = list(
                await session.scalars(
                    stmt.order_by(
                        OwnedCard.np.desc(),
                        OwnedCard.level.desc(),
                        OwnedCard.created_at,
                        OwnedCard.id,
                    ).offset(page * 8).limit(8)
                )
            )
            return cards, total, page

    async def level_up(self, user, card_id, steps, key):
        """Spend coins atomically and once per callback token; locks player before inventory."""
        if type(steps) is not int or steps not in (1, 10):
            raise GameError("Повышение доступно на 1 или 10 уровней.")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{16}", key):
            raise GameError("Кнопка устарела. Откройте карточку заново.")
        try:
            card_id = UUID(str(card_id))
        except (TypeError, ValueError, AttributeError):
            raise GameError("Карточка не найдена.") from None
        op_key = f"level:{key}"
        async with self.sessions.begin() as session:
            player, wallet = await self.wallet(session, user)
            old = await session.scalar(
                select(CardUpgrade).where(
                    CardUpgrade.player_id == player.id, CardUpgrade.key == op_key
                )
            )
            if old:
                if old.target_card_id != card_id or old.steps != steps or old.kind != "level":
                    raise GameError("Эта операция уже использована.")
                return False
            card = await session.scalar(
                select(OwnedCard)
                .where(
                    OwnedCard.id == card_id,
                    OwnedCard.player_id == player.id,
                    OwnedCard.consumed_at.is_(None),
                )
                .with_for_update()
            )
            if card is None:
                raise GameError("Карточка не найдена.")
            cost = upgrade_cost(card.level, steps)
            if wallet.balance < cost:
                raise GameError(f"Недостаточно монет. Для прокачки нужно {cost}.")
            card.level += steps
            wallet.balance -= cost
            session.add(
                CardUpgrade(
                    player_id=player.id,
                    target_card_id=card.id,
                    kind="level",
                    key=op_key,
                    steps=steps,
                    coin_cost=cost,
                )
            )
            session.add(
                CurrencyTransaction(
                    player_id=player.id,
                    kind="card_level",
                    delta=-cost,
                    balance_after=wallet.balance,
                    related_id=str(card.id),
                    key=op_key,
                )
            )
            return True

    async def fuse_np(self, user, card_id, key):
        """Consume only an unused level-1 NP-1 nonfavorite duplicate; preserve summon history."""
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{16}", key):
            raise GameError("Кнопка устарела. Откройте карточку заново.")
        try:
            card_id = UUID(str(card_id))
        except (TypeError, ValueError, AttributeError):
            raise GameError("Карточка не найдена.") from None
        op_key = f"np:{key}"
        async with self.sessions.begin() as session:
            player, _ = await self.wallet(session, user)
            old = await session.scalar(
                select(CardUpgrade).where(
                    CardUpgrade.player_id == player.id, CardUpgrade.key == op_key
                )
            )
            if old:
                if old.target_card_id != card_id or old.kind != "np":
                    raise GameError("Эта операция уже использована.")
                return False
            target = await session.scalar(
                select(OwnedCard)
                .where(
                    OwnedCard.id == card_id,
                    OwnedCard.player_id == player.id,
                    OwnedCard.consumed_at.is_(None),
                )
                .with_for_update()
            )
            if target is None:
                raise GameError("Карточка не найдена.")
            if target.np >= MAX_NP:
                raise GameError("NP уже максимальный — 5/5.")
            material = await session.scalar(
                select(OwnedCard)
                .where(
                    OwnedCard.player_id == player.id,
                    OwnedCard.character_id == target.character_id,
                    OwnedCard.id != target.id,
                    OwnedCard.consumed_at.is_(None),
                    OwnedCard.level == 1,
                    OwnedCard.np == 1,
                    OwnedCard.favorite.is_(False),
                )
                .order_by(OwnedCard.created_at, OwnedCard.id)
                .with_for_update()
                .limit(1)
            )
            if material is None:
                raise GameError(
                    "Нет подходящего дубликата. Нужна копия того же персонажа "
                    "с уровнем 1, NP 1 и без отметки «избранное»."
                )
            material.consumed_at = datetime.now(UTC)
            target.np += 1
            session.add(
                CardUpgrade(
                    player_id=player.id,
                    target_card_id=target.id,
                    source_card_id=material.id,
                    kind="np",
                    key=op_key,
                    steps=1,
                    coin_cost=0,
                )
            )
            return True

    async def favorite(self, user, card_id, value):
        async with self.sessions.begin() as session:
            result = await session.scalar(
                select(OwnedCard)
                .join(PlayerRow, PlayerRow.id == OwnedCard.player_id)
                .where(
                    PlayerRow.telegram_id == user,
                    OwnedCard.id == UUID(str(card_id)),
                    OwnedCard.consumed_at.is_(None),
                )
                .with_for_update()
            )
            if not result:
                raise GameError("Карточка не найдена.")
            result.favorite = value

    async def history(self, user):
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(Summon)
                    .join(PlayerRow)
                    .where(PlayerRow.telegram_id == user)
                    .order_by(Summon.created_at.desc())
                    .limit(10)
                )
            )

    async def character(self, character_id):
        async with self.sessions() as session:
            return await session.get(Character, character_id)

    async def illustration(self, asset_id):
        from cardlegends.infrastructure.game_models import ImageAsset

        async with self.sessions() as session:
            asset = await session.get(ImageAsset, asset_id) if asset_id else None
            return (
                asset
                if asset
                and asset.permission == "approved"
                and asset.sfw_reviewed
                and asset.content
                and asset.license
                and asset.evidence
                and asset.attribution
                else None
            )

    async def cached_file(self, key):
        from cardlegends.infrastructure.game_models import RenderCache

        async with self.sessions() as session:
            cached = await session.get(RenderCache, key)
            return cached.file_id if cached else None

    async def save_file(self, key, file_id):
        from cardlegends.infrastructure.game_models import RenderCache

        async with self.sessions.begin() as session:
            stmt = insert(RenderCache).values(key=key, file_id=file_id)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["key"], set_={"file_id": stmt.excluded.file_id}
                )
            )

    async def banner_info(self):
        async with self.sessions() as session:
            banner = await session.get(Banner, BANNER_ID)
            if not banner:
                raise GameError("Баннер не инициализирован. Обратитесь к администратору.")
            return banner
