"""Offline administration: no automatic image download at bot startup."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from cardlegends.infrastructure.art_admin import ArtAdmin
from cardlegends.infrastructure.art_sources import Danbooru, Zerochan, ZerochanAPI
from cardlegends.infrastructure.cards import render
from cardlegends.infrastructure.catalog import catalog_data, seed
from cardlegends.infrastructure.config import load_database_settings
from cardlegends.infrastructure.database import create_database
from cardlegends.infrastructure.fate_images import check_card_images, load_card_png
from cardlegends.infrastructure.game_models import Character, Franchise, ImageAsset


async def run(args):
    if args.command == "check-cards":
        issues = await asyncio.to_thread(check_card_images)
        for issue in issues:
            print(issue)
        if issues:
            raise SystemExit(1)
        print("Fate: 4/4 исходных PNG готовы к отправке.")
        return
    settings = load_database_settings()
    engine, sessions = create_database(settings.database_url.get_secret_value())
    admin = ArtAdmin(sessions)
    try:
        if args.command == "seed":
            await seed(sessions)
        elif args.command == "search":
            source = Danbooru()
            try:
                for c in catalog_data():
                    if args.slug not in ("all", c["slug"]):
                        continue
                    candidates = await source.search(
                        c["tags"]["danbooru"], c["tags"]["franchise_tag"]
                    )
                    for values in sorted(
                        candidates, key=lambda v: -v["metadata_json"]["quality_score"]
                    ):
                        await admin.save_candidate(c["slug"], values)
                    print(c["slug"], len(candidates))
            finally:
                await source.close()
        elif args.command == "zero-search":
            source = ZerochanAPI(os.environ.get("ZEROCHAN_USERNAME"))
            try:
                for candidate in await source.search(args.tag, args.franchise):
                    await admin.save_candidate(args.slug, candidate)
            finally:
                await source.close()
        elif args.command in ("list", "metadata"):
            async with sessions() as session:
                stmt = select(ImageAsset)
                if args.command == "metadata":
                    stmt = stmt.where(ImageAsset.id == UUID(args.asset))
                for asset in await session.scalars(stmt):
                    print(
                        json.dumps(
                            dict(
                                id=str(asset.id),
                                character=asset.character_slug,
                                source=asset.source,
                                page=asset.page_url,
                                permission=asset.permission,
                                artist=asset.artist,
                                license=asset.license,
                                attribution=asset.attribution,
                                metadata=asset.metadata_json,
                            ),
                            ensure_ascii=False,
                        )
                    )
        elif args.command == "manual":
            values = (
                Zerochan.candidate(args.page, args.post, args.artist)
                if args.source == "zerochan"
                else dict(
                    source="manual",
                    post_id=args.post,
                    page_url=args.page,
                    artist=args.artist,
                    permission="unknown_license",
                    metadata_json={},
                )
            )
            print(await admin.save_candidate(args.slug, values))
        elif args.command == "approve":
            await admin.approve(
                UUID(args.asset), args.license, args.evidence, args.attribution, args.sfw_reviewed
            )
        elif args.command in ("reject", "review"):
            async with sessions.begin() as session:
                asset = await session.get(ImageAsset, UUID(args.asset))
                if asset:
                    asset.permission = "rejected" if args.command == "reject" else "pending_review"
        elif args.command == "import":
            await admin.import_file(UUID(args.asset), args.file)
        elif args.command == "render":
            async with sessions() as session:
                for c in await session.scalars(select(Character)):
                    if args.slug not in ("all", c.slug):
                        continue
                    asset = (
                        await session.get(ImageAsset, c.image_asset_id)
                        if c.image_asset_id
                        else None
                    )
                    content = (
                        asset.content
                        if asset and asset.permission == "approved" and asset.sfw_reviewed
                        else None
                    )
                    if c.code:
                        original = await asyncio.to_thread(load_card_png, c.code)
                        image, filename = original.content, f"{c.code}.png"
                    else:
                        franchise = await session.get(Franchise, c.franchise_id)
                        image = await asyncio.to_thread(
                            render, c.name_ru, franchise.name, c.rarity, content
                        )
                        filename = f"{c.slug}.jpg"
                    folder = Path(args.output)
                    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
                    await asyncio.to_thread((folder / filename).write_bytes, image)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="AnimeVault Cards: каталог и разрешённые арты")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed")
    commands.add_parser("check-cards")
    search = commands.add_parser("search")
    search.add_argument("slug", help="slug или all")
    zs = commands.add_parser("zero-search")
    for field in ("slug", "tag", "franchise"):
        zs.add_argument(field)
    commands.add_parser("list")
    metadata = commands.add_parser("metadata")
    metadata.add_argument("asset")
    manual = commands.add_parser("manual")
    for field in ("slug", "source", "post", "page"):
        manual.add_argument(field)
    manual.add_argument("--artist")
    approve = commands.add_parser("approve")
    approve.add_argument("asset")
    for field in ("license", "evidence", "attribution"):
        approve.add_argument("--" + field, required=True)
    approve.add_argument("--sfw-reviewed", action="store_true", required=True)
    review = commands.add_parser("review")
    review.add_argument("asset")
    reject = commands.add_parser("reject")
    reject.add_argument("asset")
    imp = commands.add_parser("import")
    imp.add_argument("asset")
    imp.add_argument("file")
    rend = commands.add_parser("render")
    rend.add_argument("slug")
    rend.add_argument("output")
    try:
        asyncio.run(run(parser.parse_args()))
    except Exception as error:
        # Arbitrary HTTP/DB exception messages may contain credentials; never print them.
        parser.exit(1, f"Ошибка операции: {type(error).__name__}. Проверьте настройки и права.\n")


if __name__ == "__main__":
    main()
