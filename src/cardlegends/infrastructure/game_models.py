from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cardlegends.infrastructure.models import Base


class Franchise(Base):
    __tablename__ = "franchises"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class ImageAsset(Base):
    __tablename__ = "image_assets"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    character_slug: Mapped[str] = mapped_column(String(80), index=True)
    source: Mapped[str] = mapped_column(String(30))
    post_id: Mapped[str] = mapped_column(String(100))
    page_url: Mapped[str] = mapped_column(Text)
    artist: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[str | None] = mapped_column(Text)
    permission: Mapped[str] = mapped_column(String(30), default="unknown_license")
    evidence: Mapped[str | None] = mapped_column(Text)
    sfw_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("source", "post_id", "character_slug"),
        CheckConstraint("permission IN ('pending_review','approved','rejected','unknown_license')"),
    )


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    code: Mapped[str | None] = mapped_column(String(10), unique=True)
    card_class: Mapped[str | None] = mapped_column(String(50))
    collection_name: Mapped[str | None] = mapped_column(String(100))
    image_path: Mapped[str | None] = mapped_column(Text)
    limited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    base_hp: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1000, server_default="1000"
    )
    base_atk: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    base_def: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    name_ru: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
    franchise_id: Mapped[str] = mapped_column(ForeignKey("franchises.id"), index=True)
    rarity: Mapped[str] = mapped_column(String(3), index=True)
    description: Mapped[str] = mapped_column(Text)
    tags: Mapped[dict] = mapped_column(JSONB)
    image_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("image_assets.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("rarity IN ('N','R','SR','SSR','UR')"),)


class Wallet(Base):
    __tablename__ = "wallets"
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    balance: Mapped[int] = mapped_column(BigInteger)
    xp: Mapped[int] = mapped_column(BigInteger, default=0)
    training_xp: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    __table_args__ = (
        CheckConstraint("balance >= 0"),
        CheckConstraint("xp >= 0"),
        CheckConstraint("training_xp >= 0", name="ck_wallet_training_xp"),
    )


class Banner(Base):
    __tablename__ = "banners"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(String(40), unique=True)
    drop_rates: Mapped[dict | None] = mapped_column(JSONB)
    weights: Mapped[list] = mapped_column(JSONB)
    single_cost: Mapped[int] = mapped_column(Integer)
    ten_cost: Mapped[int] = mapped_column(Integer)
    guaranteed_sr: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("single_cost > 0 AND ten_cost > 0"),)


class BannerPool(Base):
    __tablename__ = "banner_pools"
    banner_id: Mapped[int] = mapped_column(ForeignKey("banners.id"), primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), primary_key=True)


class Summon(Base):
    __tablename__ = "summons"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), index=True)
    banner_id: Mapped[int] = mapped_column(ForeignKey("banners.id"))
    key: Mapped[str] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer)
    total_cost: Mapped[int | None] = mapped_column(Integer)
    balance_after: Mapped[int | None] = mapped_column(BigInteger)
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("player_id", "key"),
        UniqueConstraint("player_id", "telegram_update_id"),
        CheckConstraint("count IN (1,10)"),
        CheckConstraint("total_cost IS NULL OR total_cost > 0"),
        CheckConstraint("balance_after IS NULL OR balance_after >= 0"),
        Index("ix_summons_player_created", "player_id", "created_at"),
    )


class OwnedCard(Base):
    __tablename__ = "owned_cards"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    np: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_owned_cards_player_character", "player_id", "character_id"),
        CheckConstraint("level BETWEEN 1 AND 100", name="ck_owned_cards_level"),
        CheckConstraint("np BETWEEN 1 AND 5", name="ck_owned_cards_np"),
    )


class CardUpgrade(Base):
    """Idempotent, auditable level-up and fusion operations."""

    __tablename__ = "card_upgrades"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    target_card_id: Mapped[UUID] = mapped_column(ForeignKey("owned_cards.id"), nullable=False)
    source_card_id: Mapped[UUID | None] = mapped_column(ForeignKey("owned_cards.id"))
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    steps: Mapped[int] = mapped_column(Integer, nullable=False)
    coin_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    xp_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("player_id", "key", name="uq_card_upgrades_player_key"),
        CheckConstraint("kind IN ('level','np')", name="ck_card_upgrades_kind"),
        CheckConstraint("steps BETWEEN 1 AND 99", name="ck_card_upgrades_steps"),
        CheckConstraint("coin_cost >= 0", name="ck_card_upgrades_cost"),
        CheckConstraint("xp_cost >= 0", name="ck_card_upgrades_xp_cost"),
    )


class SummonResult(Base):
    __tablename__ = "summon_results"
    summon_id: Mapped[UUID] = mapped_column(ForeignKey("summons.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[UUID] = mapped_column(ForeignKey("owned_cards.id"), unique=True)


class CurrencyTransaction(Base):
    __tablename__ = "currency_transactions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    delta: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    related_id: Mapped[str | None] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("player_id", "key"), CheckConstraint("balance_after >= 0"))


class DailyReward(Base):
    __tablename__ = "daily_rewards"
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(ForeignKey("currency_transactions.id"))
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        UniqueConstraint("player_id", "telegram_update_id", name="uq_daily_player_update"),
    )


class RenderCache(Base):
    __tablename__ = "render_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_id: Mapped[str] = mapped_column(Text)


class SquadSlot(Base):
    """A player can assign an owned, unconsumed card to one of three combat slots."""

    __tablename__ = "squad_slots"
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[UUID] = mapped_column(ForeignKey("owned_cards.id"), nullable=False)
    __table_args__ = (
        CheckConstraint("slot BETWEEN 1 AND 3", name="ck_squad_slot"),
        UniqueConstraint("card_id", name="uq_squad_card"),
    )


class TowerProgress(Base):
    __tablename__ = "tower_progress"
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    highest_cleared: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    __table_args__ = (CheckConstraint("highest_cleared >= 0", name="ck_tower_progress_floor"),)


class TowerRun(Base):
    """Each button press is recorded once, including defeats and replay attempts."""

    __tablename__ = "tower_runs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(16), nullable=False)
    floor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    victory: Mapped[bool] = mapped_column(Boolean, nullable=False)
    turns: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_hp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_clear: Mapped[bool] = mapped_column(Boolean, server_default="false")
    report: Mapped[dict | None] = mapped_column(JSONB)
    coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("player_id", "key", name="uq_tower_runs_player_key"),
        CheckConstraint("floor >= 1", name="ck_tower_runs_floor"),
        CheckConstraint("turns BETWEEN 1 AND 60", name="ck_tower_runs_turns"),
        CheckConstraint("coins >= 0 AND xp >= 0", name="ck_tower_runs_rewards"),
        Index("ix_tower_runs_player_created", "player_id", "created_at"),
    )


class RewardGrant(Base):
    """An immutable entitlement and its single receipt, shared by every reward screen."""

    __tablename__ = "reward_grants"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(250))
    coins: Mapped[int] = mapped_column(BigInteger)
    xp: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("player_id", "key", name="uq_reward_player_key"),
        CheckConstraint("coins >= 0 AND xp >= 0", name="ck_reward_amounts"),
    )


class PlayerVisit(Base):
    __tablename__ = "player_visits"
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)


class PlayerJourney(Base):
    __tablename__ = "player_journeys"
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    tutorial_skipped: Mapped[bool] = mapped_column(Boolean, server_default="true")
    viewed_card: Mapped[bool] = mapped_column(Boolean, server_default="false")
    viewed_tasks: Mapped[bool] = mapped_column(Boolean, server_default="false")
