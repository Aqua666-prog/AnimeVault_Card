import json
from importlib.resources import files

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from cardlegends.domain.game import WEIGHTS, Economy
from cardlegends.infrastructure.game_models import Banner, BannerPool, Character, Franchise

# Shared by startup and the administrative CLI, across processes/replicas.
CATALOG_LOCK = 0x4156434154414C


class CatalogIdentityConflict(Exception):
    """A reserved ID/slug belongs to another character; never silently reuse it."""


def catalog_data():
    return json.loads(files("cardlegends").joinpath("assets/catalog.json").read_text())


async def seed(sessions, economy=None):
    """Add missing defaults atomically; never overwrite existing rows or a configured pool.

    A fully initialized DB requires only SELECT privileges. First initialization also
    requires INSERT on the four catalog tables, but no DDL/UPDATE/DELETE privileges.
    The transaction lock is released on commit, rollback, cancellation or disconnect.
    The database command timeout bounds contention: a timeout fails startup safely.
    """
    economy = economy or Economy()
    entries = catalog_data()
    async with sessions.begin() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CATALOG_LOCK})
        characters = dict((await session.execute(select(Character.id, Character.slug))).all())
        slugs = {slug: id_ for id_, slug in characters.items()}
        for entry in entries:
            if (entry["id"] in characters and characters[entry["id"]] != entry["slug"]) or (
                entry["slug"] in slugs and slugs[entry["slug"]] != entry["id"]
            ):
                raise CatalogIdentityConflict()
        franchises = set(await session.scalars(select(Franchise.id)))
        for entry in entries:
            if entry["franchise_id"] not in franchises:
                await session.execute(
                    insert(Franchise)
                    .values(id=entry["franchise_id"], name=entry["franchise"])
                    .on_conflict_do_nothing()
                )
                franchises.add(entry["franchise_id"])
            if entry["id"] not in characters:
                values = {k: v for k, v in entry.items() if k != "franchise"}
                await session.execute(insert(Character).values(**values).on_conflict_do_nothing())
        if await session.get(Banner, 1) is None:
            await session.execute(
                insert(Banner)
                .values(
                    id=1,
                    name="Начало коллекции",
                    description="Постоянный баннер • 30 персонажей",
                    weights=list(WEIGHTS),
                    single_cost=economy.single,
                    ten_cost=economy.ten,
                )
                .on_conflict_do_nothing()
            )
        # An existing nonempty pool may have been deliberately customized.
        if (
            await session.scalar(
                select(BannerPool.character_id).where(BannerPool.banner_id == 1).limit(1)
            )
            is None
        ):
            await session.execute(
                insert(BannerPool)
                .values([{"banner_id": 1, "character_id": entry["id"]} for entry in entries])
                .on_conflict_do_nothing()
            )
