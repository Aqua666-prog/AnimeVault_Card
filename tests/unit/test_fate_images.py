import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from PIL import Image

from cardlegends.bot.game_ui import send_png
from cardlegends.infrastructure.fate_images import (
    CardImageError,
    check_card_images,
    load_card_png,
)


def png(path, size=(80, 120), color="blue"):
    Image.new("RGB", size, color).save(path, format="PNG")
    return path.read_bytes()


def test_missing_files_are_precise_and_no_placeholder_is_returned(tmp_path):
    issues = check_card_images(tmp_path)
    assert len(issues) == 4
    for code in ("AV-F001", "AV-F002", "AV-F003", "AV-L001"):
        assert any(f"assets/cards/{code}.png" in issue for issue in issues)
    with pytest.raises(CardImageError):
        load_card_png("../../secrets", tmp_path)


def test_bytes_are_unchanged_and_new_content_invalidates_cache(tmp_path):
    path = tmp_path / "AV-F001.png"
    original = png(path)
    first = load_card_png("AV-F001", tmp_path)
    assert first.content == original and not first.as_document
    png(path, color="red")
    assert load_card_png("AV-F001", tmp_path).cache_key != first.cache_key
    assert original != path.read_bytes()


def test_telegram_photo_constraints_use_original_document_without_resize(tmp_path):
    path = tmp_path / "AV-L001.png"
    original = png(path, (10100, 1))
    result = load_card_png("AV-L001", tmp_path)
    assert result.as_document and result.content == original
    with Image.open(io.BytesIO(result.content)) as image:
        assert image.size == (10100, 1)


@pytest.mark.parametrize("kind", ["broken", "jpeg", "truncated", "oversized", "animated"])
def test_invalid_assets_are_rejected(tmp_path, kind, monkeypatch):
    path = tmp_path / "AV-F001.png"
    if kind == "broken":
        path.write_bytes(b"not an image")
    elif kind == "jpeg":
        Image.new("RGB", (10, 10)).save(path, format="JPEG")
    elif kind == "truncated":
        original = png(path)
        path.write_bytes(original[: len(original) // 2])
    elif kind == "animated":
        Image.new("RGB", (10, 10), "blue").save(
            path, format="PNG", save_all=True, append_images=[Image.new("RGB", (10, 10), "red")]
        )
    else:
        png(path)
        monkeypatch.setattr("cardlegends.infrastructure.fate_images.DOCUMENT_BYTES", 5)
    with pytest.raises(CardImageError):
        load_card_png("AV-F001", tmp_path)


async def test_stale_file_id_falls_back_to_original_png(tmp_path, bot):
    path = tmp_path / "AV-F001.png"
    original = png(path)
    actual_send = bot.send_photo
    attempts = []

    async def send(chat, **kwargs):
        attempts.append(kwargs["photo"])
        if isinstance(kwargs["photo"], str):
            raise TelegramBadRequest(
                method=SendPhoto(chat_id=42, photo="stale"), message="wrong file identifier"
            )
        return await actual_send(chat, **kwargs)

    event = SimpleNamespace(bot=SimpleNamespace(send_photo=send), from_user=SimpleNamespace(id=42))
    game = SimpleNamespace(
        cards_dir=tmp_path, cached_file=AsyncMock(return_value="stale"), save_file=AsyncMock()
    )
    character = SimpleNamespace(code="AV-F001")
    await send_png(event, game, character, caption="Existing card")
    assert attempts[0] == "stale"
    assert attempts[1].data == original
    assert path.read_bytes() == original


async def test_check_cards_cli_needs_no_db_or_telegram(monkeypatch, capsys, tmp_path):
    from cardlegends.cli import run

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setattr("cardlegends.infrastructure.fate_images.cards_root", lambda: tmp_path)
    with pytest.raises(SystemExit) as error:
        await run(SimpleNamespace(command="check-cards"))
    assert error.value.code == 1
    assert "assets/cards/AV-F001.png" in capsys.readouterr().out
    for code in ("AV-F001", "AV-F002", "AV-F003", "AV-L001"):
        png(tmp_path / f"{code}.png")
    await run(SimpleNamespace(command="check-cards"))
    assert "4/4" in capsys.readouterr().out


async def test_large_photo_is_sent_as_original_png_document_and_cached(tmp_path):
    from datetime import UTC, datetime

    from aiogram.types import Chat, Document, Message

    original = png(tmp_path / "AV-L001.png", (10100, 1))
    response = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        document=Document(file_id="document-id", file_unique_id="unique"),
    )
    send = AsyncMock(return_value=response)
    event = SimpleNamespace(
        bot=SimpleNamespace(send_document=send), from_user=SimpleNamespace(id=42)
    )
    game = SimpleNamespace(
        cards_dir=tmp_path, cached_file=AsyncMock(return_value=None), save_file=AsyncMock()
    )
    await send_png(event, game, SimpleNamespace(code="AV-L001"), caption="UR Limited")
    upload = send.call_args.kwargs["document"]
    assert upload.filename == "AV-L001.png" and upload.data == original
    game.save_file.assert_awaited_once_with(
        load_card_png("AV-L001", tmp_path).cache_key, "document-id"
    )


async def test_cache_write_error_does_not_fail_successful_delivery_or_log_secrets(tmp_path, caplog):
    from datetime import UTC, datetime

    from aiogram.types import Chat, Message, PhotoSize

    png(tmp_path / "AV-F001.png")
    response = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        photo=[PhotoSize(file_id="photo-id", file_unique_id="u", width=80, height=120)],
    )
    send = AsyncMock(return_value=response)
    event = SimpleNamespace(bot=SimpleNamespace(send_photo=send), from_user=SimpleNamespace(id=42))
    game = SimpleNamespace(
        cards_dir=tmp_path,
        cached_file=AsyncMock(return_value=None),
        save_file=AsyncMock(side_effect=RuntimeError("SECRET-DB-URL")),
    )
    await send_png(event, game, SimpleNamespace(code="AV-F001"), caption="SSR")
    send.assert_awaited_once()
    assert "SECRET-DB-URL" not in caplog.text


async def test_cache_read_failure_still_uses_local_original(tmp_path, bot):
    original = png(tmp_path / "AV-F001.png")
    event = SimpleNamespace(bot=bot, from_user=SimpleNamespace(id=42))
    game = SimpleNamespace(
        cards_dir=tmp_path,
        cached_file=AsyncMock(side_effect=RuntimeError("internal cache failure")),
        save_file=AsyncMock(),
    )
    await send_png(event, game, SimpleNamespace(code="AV-F001"), caption="SSR")
    assert bot.session.calls[-1].photo.data == original
