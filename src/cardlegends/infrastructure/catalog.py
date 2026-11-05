import json
from importlib.resources import files

from sqlalchemy.dialects.postgresql import insert

from cardlegends.domain.game import WEIGHTS, Economy
from cardlegends.infrastructure.game_models import Banner, BannerPool, Character, Franchise


def catalog_data():
    return json.loads(files("cardlegends").joinpath("assets/catalog.json").read_text())


async def seed(sessions, economy=None):
    economy = economy or Economy()
    async with sessions.begin() as session:
        for entry in catalog_data():
            await session.execute(
                insert(Franchise)
                .values(id=entry["franchise_id"], name=entry["franchise"])
                .on_conflict_do_nothing()
            )
            values = {k: v for k, v in entry.items() if k != "franchise"}
            await session.execute(insert(Character).values(**values).on_conflict_do_nothing())
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
        for entry in catalog_data():
            await session.execute(
                insert(BannerPool)
                .values(banner_id=1, character_id=entry["id"])
                .on_conflict_do_nothing()
            )
