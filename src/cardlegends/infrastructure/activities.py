"""Persistent reward hub. All writes lock the same player row as existing gameplay."""

from datetime import UTC
from uuid import UUID

from sqlalchemy import BigInteger, case, cast, func, select
from sqlalchemy.dialects.postgresql import insert

from cardlegends.domain.activities import (
    ACHIEVEMENTS,
    DAILY,
    MILESTONES,
    WEEKLY,
    calendar_bonus,
    credit,
    period,
)
from cardlegends.domain.fate import CARD_IDS
from cardlegends.domain.game import GameError
from cardlegends.infrastructure.game_models import (
    CardUpgrade,
    Character,
    CurrencyTransaction,
    DailyReward,
    OwnedCard,
    PlayerJourney,
    PlayerVisit,
    RewardGrant,
    SquadSlot,
    Summon,
    TowerProgress,
    TowerRun,
)
from cardlegends.infrastructure.models import PlayerRow


class Activities:
    """Mixin keeps SQL/application logic out of Telegram renderers."""

    async def _now(self, session):
        return (await session.scalar(select(func.clock_timestamp()))).astimezone(UTC)

    async def franchise_options(self):
        from cardlegends.infrastructure.game_models import Franchise

        async with self.sessions() as s:
            return list(await s.scalars(select(Franchise).order_by(Franchise.id)))

    async def visit(self, user):
        async with self.sessions.begin() as s:
            player, _ = await self.wallet(s, user)
            now = await self._now(s)
            await s.execute(
                insert(PlayerVisit)
                .values(player_id=player.id, day=now.date())
                .on_conflict_do_nothing()
            )
            await s.execute(
                insert(PlayerJourney)
                .values(player_id=player.id, tutorial_skipped=False)
                .on_conflict_do_nothing()
            )

    async def facts(self, s, player_id, start=None, end=None):
        async def count(model, expression, *conditions):
            stmt = select(expression).where(model.player_id == player_id, *conditions)
            if start is not None:
                field = model.day if model in (PlayerVisit, DailyReward) else model.created_at
                lo, hi = (
                    (start.date(), end.date())
                    if model in (PlayerVisit, DailyReward)
                    else (start, end)
                )
                stmt = stmt.where(field >= lo, field < hi)
            return int(await s.scalar(stmt) or 0)

        result = {
            "login": await count(PlayerVisit, func.count()),
            "daily": await count(DailyReward, func.count()),
            "summons": await count(Summon, func.sum(Summon.count)),
            "battles": await count(TowerRun, func.count()),
            "wins": await count(TowerRun, func.count(), TowerRun.victory.is_(True)),
            "floors": await count(TowerRun, func.count(), TowerRun.first_clear.is_(True)),
            "levels": await count(
                CardUpgrade, func.sum(CardUpgrade.steps), CardUpgrade.kind == "level"
            ),
        }
        if start is None:
            result.update(
                cards=await count(OwnedCard, func.count()),
                level=await count(OwnedCard, func.max(OwnedCard.level)),
                np=await count(OwnedCard, func.max(OwnedCard.np)),
            )
            result["ur"] = int(
                await s.scalar(
                    select(func.count())
                    .select_from(OwnedCard)
                    .join(Character)
                    .where(OwnedCard.player_id == player_id, Character.rarity == "UR")
                )
                or 0
            )
            result["fate"] = int(
                await s.scalar(
                    select(func.count(func.distinct(Character.code)))
                    .select_from(OwnedCard)
                    .join(Character)
                    .where(OwnedCard.player_id == player_id, Character.code.in_(CARD_IDS))
                )
                or 0
            )
            progress = await s.get(TowerProgress, player_id)
            result["floor"] = progress.highest_cleared if progress else 0
        return result

    async def _offer(self, s, player_id, key, category, reason, coins, xp, expires=None):
        await s.execute(
            insert(RewardGrant)
            .values(
                player_id=player_id,
                key=key,
                category=category,
                reason=reason,
                coins=coins,
                xp=xp,
                expires_at=expires,
            )
            .on_conflict_do_nothing(index_elements=["player_id", "key"])
        )

    async def _sync(self, s, player_id, now):
        boards = {}
        for scope, definitions in (
            ("daily", DAILY),
            ("weekly", WEEKLY),
            ("achievements", ACHIEVEMENTS),
        ):
            start, end = period(scope, now) if scope != "achievements" else (None, None)
            facts = await self.facts(s, player_id, start, end)
            rows = []
            for obj in definitions:
                key = f"{scope}:{start.date() if start else 'ever'}:{obj.id}"
                value = facts.get(obj.metric, 0)
                if value >= obj.target:
                    await self._offer(s, player_id, key, scope, obj.name, obj.coins, obj.xp, end)
                rows.append((obj, value, key))
            boards[scope] = rows
        for floor, (coins, xp) in MILESTONES.items():
            if facts["floor"] >= floor:
                await self._offer(
                    s,
                    player_id,
                    f"milestone:{floor}",
                    "milestone",
                    f"Башня: этаж {floor}",
                    coins,
                    xp,
                )
        return boards

    async def reward_board(self, user, scope="daily"):
        if scope not in ("daily", "weekly", "achievements", "all", "mail", "calendar"):
            raise GameError("Раздел наград не найден.")
        async with self.sessions.begin() as s:
            player, _ = await self.wallet(s, user)
            now = await self._now(s)
            boards = await self._sync(s, player.id, now)
            grants = list(
                await s.scalars(
                    select(RewardGrant)
                    .where(RewardGrant.player_id == player.id)
                    .order_by(RewardGrant.created_at.desc(), RewardGrant.id)
                )
            )
            return boards.get(scope, []), grants, now

    async def claim_rewards(self, user, reward_id=None, category=None):
        if category not in (None, "mail", "daily", "weekly", "achievements", "calendar"):
            raise GameError("Неизвестная награда.")
        async with self.sessions.begin() as s:
            player, wallet = await self.wallet(s, user)
            now = await self._now(s)
            await self._sync(s, player.id, now)
            stmt = select(RewardGrant).where(RewardGrant.player_id == player.id)
            if reward_id is not None:
                try:
                    stmt = stmt.where(RewardGrant.id == UUID(str(reward_id)))
                except ValueError:
                    raise GameError("Награда не найдена.") from None
            else:
                stmt = stmt.where(
                    RewardGrant.claimed_at.is_(None),
                    (RewardGrant.expires_at.is_(None)) | (RewardGrant.expires_at > now),
                )
                if category:
                    stmt = stmt.where(RewardGrant.category == category)
            grants = list(
                await s.scalars(
                    stmt.order_by(RewardGrant.created_at, RewardGrant.id).with_for_update()
                )
            )
            if reward_id is not None:
                if not grants:
                    raise GameError("Награда не найдена.")
                if grants[0].claimed_at:
                    return 0, 0, 0
                if grants[0].expires_at and grants[0].expires_at <= now:
                    raise GameError("Период завершён. Откройте текущие задания.")
            coins, xp = sum(g.coins for g in grants), sum(g.xp for g in grants)
            credit(wallet, coins, xp)
            cursor = wallet.balance - coins
            for g in grants:
                cursor += g.coins
                g.claimed_at = now
                s.add(
                    CurrencyTransaction(
                        player_id=player.id,
                        kind="reward",
                        delta=g.coins,
                        balance_after=cursor,
                        related_id=str(g.id),
                        key=f"reward:{g.id.hex}",
                    )
                )
            # Surface ledger/constraint errors while still inside the transaction.
            await s.flush()
            return len(grants), coins, xp

    async def reward_detail(self, user, reward_id):
        try:
            reward_id = UUID(str(reward_id))
        except ValueError:
            raise GameError("Письмо не найдено.") from None
        async with self.sessions() as s:
            grant = await s.scalar(
                select(RewardGrant)
                .join(PlayerRow)
                .where(PlayerRow.telegram_id == user, RewardGrant.id == reward_id)
            )
            if grant is None:
                raise GameError("Письмо не найдено.")
            return grant, await self._now(s)

    async def gift(self, user, key, reason, coins, xp):
        # Only the authenticated offline CLI calls this; Telegram never accepts reward amounts.
        if not isinstance(key, str) or not 1 <= len(key) <= 60 or not reason or len(reason) > 250:
            raise GameError("Укажите ключ подарка до 60 символов и причину до 250.")
        if any(type(x) is not int or not 0 <= x <= 1_000_000_000 for x in (coins, xp)):
            raise GameError("Неверный размер подарка.")
        async with self.sessions.begin() as s:
            player, _ = await self.wallet(s, user)
            old = await s.scalar(
                select(RewardGrant).where(
                    RewardGrant.player_id == player.id, RewardGrant.key == f"mail:{key}"
                )
            )
            if old and (old.coins, old.xp, old.reason) != (coins, xp, reason):
                raise GameError("Ключ уже используется другим подарком.")
            await self._offer(s, player.id, f"mail:{key}", "mail", reason, coins, xp)

    async def calendar(self, user):
        async with self.sessions.begin() as s:
            player, _ = await self.wallet(s, user)
            now = await self._now(s)
            total = int(
                await s.scalar(
                    select(func.count())
                    .select_from(DailyReward)
                    .where(DailyReward.player_id == player.id)
                )
                or 0
            )
            claimed = await s.get(DailyReward, (player.id, now.date())) is not None
            return total, claimed, calendar_bonus(total if not claimed else max(0, total - 1))

    async def tutorial(self, user, action=None):
        async with self.sessions.begin() as s:
            player, wallet = await self.wallet(s, user)
            journey = await s.get(PlayerJourney, player.id)
            if journey is None:
                journey = PlayerJourney(
                    player_id=player.id,
                    tutorial_skipped=True,
                    viewed_card=False,
                    viewed_tasks=False,
                )
                s.add(journey)
            if action == "skip":
                journey.tutorial_skipped = True
            elif action == "open":
                journey.tutorial_skipped = False
            elif action == "card":
                journey.viewed_card = True
            elif action == "tasks":
                journey.viewed_tasks = True
            facts = await self.facts(s, player.id)
            team = int(
                await s.scalar(
                    select(func.count())
                    .select_from(SquadSlot)
                    .where(SquadSlot.player_id == player.id)
                )
                or 0
            )
            steps = [
                True,
                facts["cards"] > 0,
                journey.viewed_card,
                team > 0,
                facts["wins"] > 0,
                wallet.xp > 0,
                facts["levels"] > 0,
                journey.viewed_tasks,
            ]
            return journey.tutorial_skipped, steps

    @staticmethod
    def _power_expression():
        factor = (100 + 2 * (OwnedCard.level - 1)) * (100 + 15 * (OwnedCard.np - 1))

        def stat(column):
            return (cast(column, BigInteger) * factor).op("/")(10000)

        return stat(Character.base_hp) + 5 * stat(Character.base_atk) + 3 * stat(Character.base_def)

    async def inventory(self, user, page=0, sort="power", filter_="all", value="all"):
        if sort not in ("power", "rarity", "level", "np", "time") or filter_ not in (
            "all",
            "rarity",
            "character",
            "franchise",
            "level",
            "np",
            "favorite",
            "team",
            "duplicates",
        ):
            raise GameError("Фильтр устарел.")
        if type(page) is not int or page < 0:
            raise GameError("Неверная страница.")
        async with self.sessions() as s:
            player = await s.scalar(select(PlayerRow).where(PlayerRow.telegram_id == user))
            if not player:
                raise GameError("Сначала нажмите /start.")
            team = set(
                await s.scalars(select(SquadSlot.card_id).where(SquadSlot.player_id == player.id))
            )
            stmt = (
                select(OwnedCard, Character)
                .join(Character)
                .where(OwnedCard.player_id == player.id, OwnedCard.consumed_at.is_(None))
            )
            if filter_ == "rarity":
                stmt = stmt.where(Character.rarity == value)
            elif filter_ == "character":
                stmt = stmt.where(Character.code == value)
            elif filter_ == "franchise":
                stmt = stmt.where(Character.franchise_id == value)
            elif filter_ in ("level", "np"):
                if not value.isdigit() or not 1 <= int(value) <= (100 if filter_ == "level" else 5):
                    raise GameError("Некорректный фильтр развития.")
                stmt = stmt.where(
                    (OwnedCard.level if filter_ == "level" else OwnedCard.np) >= int(value)
                )
            elif filter_ == "favorite":
                stmt = stmt.where(OwnedCard.favorite.is_(True))
            elif filter_ == "team":
                stmt = stmt.where(OwnedCard.id.in_(team))
            elif filter_ == "duplicates":
                duplicate_ids = (
                    select(OwnedCard.character_id)
                    .where(OwnedCard.player_id == player.id, OwnedCard.consumed_at.is_(None))
                    .group_by(OwnedCard.character_id)
                    .having(func.count() > 1)
                    .correlate(None)
                )
                stmt = stmt.where(Character.id.in_(duplicate_ids))
            rank = {
                "power": self._power_expression(),
                "level": OwnedCard.level,
                "np": OwnedCard.np,
                "time": OwnedCard.created_at,
                "rarity": case(
                    {r: i for i, r in enumerate(("N", "R", "SR", "SSR", "UR"))},
                    value=Character.rarity,
                ),
            }[sort]
            total = int(await s.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
            page = min(page, max(0, (total - 1) // 6))
            rows = (
                await s.execute(
                    stmt.order_by(rank.desc(), OwnedCard.created_at.desc(), OwnedCard.id)
                    .offset(page * 6)
                    .limit(6)
                )
            ).all()
            return list(rows), total, page, team

    async def profile_v2(self, user):
        data = await self.profile(user)
        async with self.sessions() as s:
            pid = data["player"].id
            facts = await self.facts(s, pid)
            active = (
                select(OwnedCard, Character)
                .join(Character)
                .where(OwnedCard.player_id == pid, OwnedCard.consumed_at.is_(None))
            )
            total, unique = (
                await s.execute(
                    select(
                        func.count(OwnedCard.id), func.count(func.distinct(OwnedCard.character_id))
                    ).where(OwnedCard.player_id == pid, OwnedCard.consumed_at.is_(None))
                )
            ).one()
            strongest = (
                await s.execute(
                    active.order_by(self._power_expression().desc(), OwnedCard.id).limit(1)
                )
            ).first()
            data.update(
                highest=facts["floor"],
                wins=facts["wins"],
                losses=facts["battles"] - facts["wins"],
                achievements=sum(facts.get(a.metric, 0) >= a.target for a in ACHIEVEMENTS),
            )
            data["total_all"] = total
            data["unique_all"] = unique
            data["strongest"] = strongest
            return data
