from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from cardlegends.domain.game import Economy
from cardlegends.domain.players import PersistenceError, Player
from cardlegends.infrastructure.game_models import CurrencyTransaction, Wallet
from cardlegends.infrastructure.models import PlayerRow


def to_player(row: PlayerRow) -> Player:
    return Player(
        row.id,
        row.telegram_id,
        row.display_name,
        row.username,
        row.created_at,
        row.updated_at,
        row.last_seen_at,
    )


class PostgresPlayerRepository:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self.sessions = sessions

    async def upsert(self, telegram_id: int, display_name: str, username: str | None) -> Player:
        statement = insert(PlayerRow).values(
            telegram_id=telegram_id,
            display_name=display_name,
            username=username,
            last_seen_at=func.now(),
        )
        statement = statement.on_conflict_do_nothing(constraint="uq_players_telegram_id").returning(
            PlayerRow
        )
        try:
            async with self.sessions.begin() as session:
                row = (await session.scalars(statement)).one_or_none()
                if row is not None:
                    # The account and its one-time grant are committed together.
                    initial = Economy().initial
                    session.add(Wallet(player_id=row.id, balance=initial, xp=0))
                    session.add(
                        CurrencyTransaction(
                            player_id=row.id,
                            kind="initial",
                            delta=initial,
                            balance_after=initial,
                            key="initial",
                        )
                    )
                else:
                    row = (
                        await session.scalars(
                            update(PlayerRow)
                            .where(PlayerRow.telegram_id == telegram_id)
                            .values(
                                display_name=display_name,
                                username=username,
                                updated_at=func.clock_timestamp(),
                                last_seen_at=func.now(),
                            )
                            .returning(PlayerRow)
                        )
                    ).one()
                player = to_player(row)
            return player
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            raise PersistenceError from exc

    async def get_by_telegram_id(self, telegram_id: int) -> Player | None:
        try:
            async with self.sessions() as session:
                row = await session.scalar(
                    select(PlayerRow).where(PlayerRow.telegram_id == telegram_id)
                )
                return to_player(row) if row is not None else None
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            raise PersistenceError from exc
