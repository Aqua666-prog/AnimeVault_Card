from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

from cardlegends.domain.game import RARITIES, Economy, GameError, draw
from cardlegends.infrastructure.game_models import (
    Banner,
    BannerPool,
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
    def __init__(self, sessions, economy=None, rng=None):
        self.sessions, self.economy, self.rng = sessions, economy or Economy(), rng

    async def wallet(self, session, telegram_id):
        # Lock player first: all economic operations use the same lock order.
        player = await session.scalar(
            select(PlayerRow).where(PlayerRow.telegram_id == telegram_id).with_for_update()
        )
        if not player:
            raise GameError("Сначала нажмите /start.")
        created = await session.scalar(
            insert(Wallet)
            .values(player_id=player.id, balance=self.economy.initial, xp=0)
            .on_conflict_do_nothing()
            .returning(Wallet.player_id)
        )
        if created:
            session.add(
                CurrencyTransaction(
                    player_id=player.id,
                    kind="initial",
                    delta=self.economy.initial,
                    balance_after=self.economy.initial,
                    key="initial",
                )
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
                    ).where(OwnedCard.player_id == player.id)
                )
            ).one()
            summons = await session.scalar(
                select(func.count()).select_from(Summon).where(Summon.player_id == player.id)
            )
            size = await session.scalar(
                select(func.count()).select_from(Character).where(Character.is_active.is_(True))
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

    async def summon(self, user, count, key):
        if count not in (1, 10) or len(key) > 64:
            raise GameError("Неверный призыв.")
        async with self.sessions.begin() as session:
            player, wallet = await self.wallet(session, user)
            old = await session.scalar(
                select(Summon).where(Summon.player_id == player.id, Summon.key == key)
            )
            if old:
                return old.id
            banner = await session.get(Banner, 1)
            now = datetime.now(UTC)
            if (
                not banner
                or not banner.active
                or (banner.starts_at and now < banner.starts_at)
                or (banner.ends_at and now >= banner.ends_at)
                or not banner.guaranteed_sr
            ):
                raise GameError("Баннер недоступен.")
            characters = list(
                await session.scalars(
                    select(Character)
                    .join(BannerPool, BannerPool.character_id == Character.id)
                    .where(BannerPool.banner_id == banner.id, Character.is_active.is_(True))
                )
            )
            pool = {r: [c for c in characters if c.rarity == r] for r in RARITIES}
            cost = banner.single_cost if count == 1 else banner.ten_cost
            if wallet.balance < cost:
                raise GameError("Недостаточно кристаллов. Получите ежедневную награду.")
            chosen = draw(pool, count, banner.weights, self.rng)
            wallet.balance -= cost
            wallet.xp += count * self.economy.xp_per_card
            summon = Summon(player_id=player.id, banner_id=banner.id, key=key, count=count)
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
            for position, character in enumerate(chosen):
                card = OwnedCard(player_id=player.id, character_id=character.id)
                session.add(card)
                await session.flush()
                session.add(SummonResult(summon_id=summon.id, position=position, card_id=card.id))
            return summon.id

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
        self, user, owned=False, page=0, rarity="all", franchise="all", sort="rarity", fav=False
    ):
        async with self.sessions() as session:
            if owned:
                stmt = (
                    select(OwnedCard, Character)
                    .join(Character)
                    .join(PlayerRow, PlayerRow.id == OwnedCard.player_id)
                    .where(PlayerRow.telegram_id == user)
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
                    .where(PlayerRow.telegram_id == user, OwnedCard.id == UUID(str(card_id)))
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
                )
            )
            return *result, copies

    async def favorite(self, user, card_id, value):
        async with self.sessions.begin() as session:
            result = await session.scalar(
                select(OwnedCard)
                .join(PlayerRow, PlayerRow.id == OwnedCard.player_id)
                .where(PlayerRow.telegram_id == user, OwnedCard.id == UUID(str(card_id)))
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
            banner = await session.get(Banner, 1)
            if not banner:
                raise GameError("Баннер не инициализирован. Обратитесь к администратору.")
            return banner
