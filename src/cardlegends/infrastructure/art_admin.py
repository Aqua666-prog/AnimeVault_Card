import asyncio
import io
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from cardlegends.infrastructure.cards import MAX_BYTES, validate_image
from cardlegends.infrastructure.game_models import Character, ImageAsset


class ArtAdmin:
    def __init__(self, sessions):
        self.sessions = sessions

    async def save_candidate(self, slug, values):
        async with self.sessions.begin() as session:
            statement = insert(ImageAsset).values(character_slug=slug, **values)
            statement = statement.on_conflict_do_nothing().returning(ImageAsset.id)
            return await session.scalar(statement)

    async def approve(self, asset_id, license, evidence, attribution, sfw):
        if not all((license, evidence, attribution, sfw)):
            raise ValueError("Нужны лицензия, доказательство прав, атрибуция и SFW-проверка.")
        async with self.sessions.begin() as session:
            asset = await session.get(ImageAsset, asset_id)
            if not asset:
                raise ValueError("Кандидат не найден.")
            if len(attribution) > 300 or len(asset.page_url) > 250:
                raise ValueError(
                    "Атрибуция и ссылка должны полностью помещаться в подпись карточки."
                )
            asset.license, asset.evidence, asset.attribution = license, evidence, attribution
            asset.permission = "approved"
            asset.sfw_reviewed = True

    async def import_file(self, asset_id, path):
        # Operator supplies a lawfully obtained file. Never download unlicensed candidates.
        def read_bounded():
            with Path(path).open("rb") as stream:
                return stream.read(MAX_BYTES + 1)

        data = await asyncio.to_thread(read_bounded)
        await asyncio.to_thread(validate_image, data)
        async with self.sessions.begin() as session:
            asset = await session.get(ImageAsset, asset_id)
            if (
                not asset
                or asset.permission != "approved"
                or not asset.sfw_reviewed
                or not asset.evidence
            ):
                raise ValueError("Импорт запрещён без подтверждения прав и SFW.")

            # Store compressed source in PostgreSQL, not ephemeral local disk.
            def compress():
                with Image.open(io.BytesIO(data)) as image:
                    image = image.convert("RGB")
                    image.thumbnail((1200, 1600))
                    output = io.BytesIO()
                    image.save(output, "JPEG", quality=90)
                    return output.getvalue()

            asset.content = await asyncio.to_thread(compress)
            character = await session.scalar(
                select(Character).where(Character.slug == asset.character_slug)
            )
            if not character:
                raise ValueError("Персонаж не найден.")
            character.image_asset_id = asset.id
