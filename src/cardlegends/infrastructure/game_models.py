from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
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
    balance: Mapped[int] = mapped_column(Integer)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("balance >= 0"), CheckConstraint("xp >= 0"))


class Banner(Base):
    __tablename__ = "banners"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("player_id", "key"), CheckConstraint("count IN (1,10)"))


class OwnedCard(Base):
    __tablename__ = "owned_cards"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    related_id: Mapped[str | None] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("player_id", "key"), CheckConstraint("balance_after >= 0"))


class DailyReward(Base):
    __tablename__ = "daily_rewards"
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(ForeignKey("currency_transactions.id"))


class RenderCache(Base):
    __tablename__ = "render_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_id: Mapped[str] = mapped_column(Text)
