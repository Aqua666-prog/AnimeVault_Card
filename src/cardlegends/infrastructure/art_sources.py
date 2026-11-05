"""Metadata discovery; downloading is a separate explicit rights-gated operation."""

import asyncio
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

BLOCKED = {
    "nsfw",
    "guro",
    "gore",
    "nude",
    "nudity",
    "underwear",
    "panties",
    "cleavage",
    "loli",
    "shota",
    "sex",
    "suggestive",
    "watermark",
    "text_focus",
    "multiple_girls",
    "multiple_boys",
}


class SourceError(Exception):
    pass


def backoff(response, attempt):
    value = response.headers.get("Retry-After", "")
    delay = 2**attempt
    if value:
        try:
            delay = (
                int(value)
                if value.isdigit()
                else (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            )
        except (ValueError, TypeError, OverflowError):
            pass
    if delay > 30:
        raise SourceError("Источник требует длительную паузу. Повторите запрос позднее.")
    return max(0, delay)


class Danbooru:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(
            timeout=20,
            follow_redirects=False,
            headers={
                "User-Agent": "AnimeVaultCards/0.2 (metadata research; rights reviewed manually)"
            },
        )
        self.last = 0

    async def request(self, path, params=None):
        for attempt in range(4):
            await asyncio.sleep(max(0, 1 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            response = await self.client.get("https://danbooru.donmai.us" + path, params=params)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                await asyncio.sleep(backoff(response, attempt))
                continue
            if response.status_code != 200:
                raise SourceError("Источник недоступен или ограничил публичный доступ.")
            return response.json()
        raise SourceError("Источник временно недоступен после повторных попыток.")

    async def search(self, character_tag, franchise_tag):
        # Verify the configured canonical tag exists; never manufacture tags from names.
        tags = await self.request("/tags.json", {"search[name]": character_tag, "limit": 10})
        if not any(t["name"] == character_tag and t.get("category") == 4 for t in tags):
            raise SourceError("Канонический тег персонажа не подтверждён API.")
        posts = await self.request(
            "/posts.json",
            {
                "tags": f"{character_tag} rating:g",
                "limit": 20,
            },
        )
        return [
            self.candidate(p, character_tag, franchise_tag)
            for p in posts
            if self.safe(p, character_tag, franchise_tag)
        ]

    @staticmethod
    def safe(post, character_tag, franchise_tag):
        tags = set(post.get("tag_string", "").split())
        return (
            post.get("rating") == "g"
            and {character_tag, franchise_tag} <= tags
            and not tags.intersection(BLOCKED)
            and not post.get("is_deleted")
            and post.get("image_width", 0) >= 500
            and post.get("image_height", 0) >= 700
            and post.get("file_ext") in {"jpg", "png", "webp"}
            and post.get("tag_string_character", "").split() == [character_tag]
        )

    @staticmethod
    def candidate(post, *_):
        width, height = post["image_width"], post["image_height"]
        return dict(
            source="danbooru",
            post_id=str(post["id"]),
            page_url=f"https://danbooru.donmai.us/posts/{post['id']}",
            artist=post.get("tag_string_artist"),
            license=None,
            permission="unknown_license",
            metadata_json={
                "file_url": post.get("file_url"),
                "source_url": post.get("source"),
                "tags": post.get("tag_string"),
                "width": width,
                "height": height,
                "quality_score": min(width, height) + (500 if height > width else 0),
                "rating": post.get("rating"),
            },
        )

    async def close(self):
        await self.client.aclose()


class Zerochan:
    """Manual metadata/file import only. No undocumented API, scraping or bypass."""

    @staticmethod
    def candidate(page_url, post_id, artist=None):
        parsed = urlparse(page_url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.zerochan.net", "zerochan.net"}:
            raise SourceError("Нужна HTTPS-ссылка страницы Zerochan.")
        if parsed.path.strip("/") != str(post_id) or not str(post_id).isdigit():
            raise SourceError("ID не соответствует странице Zerochan.")
        return dict(
            source="zerochan",
            post_id=str(post_id),
            page_url=page_url,
            artist=artist,
            permission="unknown_license",
            metadata_json={},
        )


class ZerochanAPI(Danbooru):
    """Official read-only API; account name in User-Agent is mandatory per /api."""

    def __init__(self, username, client=None):
        if not username or any(c in username for c in "\r\n"):
            raise SourceError("Укажите собственный Zerochan username для User-Agent.")
        super().__init__(
            client
            or httpx.AsyncClient(
                timeout=20,
                follow_redirects=False,
                headers={"User-Agent": f"AnimeVaultCards - {username}"},
            )
        )

    async def request(self, path, params=None):
        for attempt in range(4):
            await asyncio.sleep(max(0, 1.1 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            response = await self.client.get(
                "https://www.zerochan.net" + path, params=params or {"json": ""}
            )
            if response.status_code == 429 or response.status_code >= 500:
                await asyncio.sleep(backoff(response, attempt))
                continue
            if response.status_code != 200:
                raise SourceError("Zerochan API ограничен; используйте разрешённый ручной импорт.")
            return response.json()
        raise SourceError("Zerochan API временно недоступен.")

    async def search(self, character_tag, franchise_tag):
        from urllib.parse import quote

        data = await self.request(
            "/" + quote(character_tag + "," + franchise_tag, safe=""),
            {"json": "", "l": 20, "d": "portrait"},
        )
        results = []
        for item in data.get("items", []):
            post = await self.request("/" + str(int(item["id"])))
            tags = post.get("tags", [])
            if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                continue
            lowered = {t.lower() for t in tags}
            if post.get("width", 0) < 500 or post.get("height", 0) < 700:
                continue
            if {character_tag, franchise_tag} <= set(tags) and not lowered.intersection(
                BLOCKED | {"ecchi", "sexually suggestive", "explicit", "gore"}
            ):
                candidate = Zerochan.candidate(
                    "https://www.zerochan.net/" + str(post["id"]), str(post["id"])
                )
                candidate["metadata_json"] = {
                    "tags": tags,
                    "preview_url": post.get("thumbnail"),
                    "width": post.get("width"),
                    "height": post.get("height"),
                    "sfw": "requires_human_review",
                }
                results.append(candidate)
        return results
