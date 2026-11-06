import json
from importlib.resources import files

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from cardlegends.domain.fate import BANNER_CODE, BANNER_ID, BANNER_NAME, DROP_RATES
from cardlegends.infrastructure.game_models import Banner, BannerPool, Character, Franchise

CATALOG_LOCK = 0x4156434154414C


class CatalogIdentityConflict(Exception):
    """Reserved technical identities conflict with operator data; do not overwrite."""


def catalog_data():
    return json.loads(files("cardlegends").joinpath("assets/catalog.json").read_text())


async def seed(sessions, economy=None):
    """Serialize startup/CLI; add exactly four Fate cards, preserving all legacy rows.

    Completed installations need only SELECT. First initialization additionally needs
    INSERT on franchises/characters/banners/banner_pools, never UPDATE/DELETE or DDL.
    """
    entries = catalog_data()
    async with sessions.begin() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CATALOG_LOCK})
        existing = list(await session.scalars(select(Character)))
        for entry in entries:
            collisions = [
                c
                for c in existing
                if c.id == entry["id"] or c.slug == entry["slug"] or c.code == entry["code"]
            ]
            if any(
                (c.id, c.slug, c.code) != (entry["id"], entry["slug"], entry["code"])
                for c in collisions
            ):
                raise CatalogIdentityConflict()
        banners = list(
            await session.scalars(
                select(Banner).where((Banner.id == BANNER_ID) | (Banner.code == BANNER_CODE))
            )
        )
        if any(b.id != BANNER_ID or b.code != BANNER_CODE for b in banners):
            raise CatalogIdentityConflict()
        if await session.get(Franchise, "fate") is None:
            await session.execute(insert(Franchise).values(id="fate", name="Fate"))
        ids = {c.id for c in existing}
        for entry in entries:
            if entry["id"] not in ids:
                await session.execute(
                    insert(Character).values(**{k: v for k, v in entry.items() if k != "franchise"})
                )
        if not banners:
            await session.execute(
                insert(Banner).values(
                    id=BANNER_ID,
                    code=BANNER_CODE,
                    name=BANNER_NAME,
                    description="Fate Collection: 4 карточки, без гарантий и pity.",
                    weights=[0, 0, 0, 99, 1],
                    drop_rates=DROP_RATES,
                    single_cost=100,
                    ten_cost=1000,
                    guaranteed_sr=False,
                )
            )
        pool = set(
            await session.scalars(
                select(BannerPool.character_id).where(BannerPool.banner_id == BANNER_ID)
            )
        )
        expected = {e["id"] for e in entries}
        if pool - expected:
            raise CatalogIdentityConflict()
        for id_ in sorted(expected - pool):
            await session.execute(insert(BannerPool).values(banner_id=BANNER_ID, character_id=id_))
