"""Original PNG delivery, without remote dependencies, redraws or placeholders."""

import hashlib
import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from cardlegends.domain.fate import DROP_RATES
from cardlegends.domain.game import GameError

PHOTO_BYTES = 10_000_000
DOCUMENT_BYTES = 50_000_000
MAX_PIXELS = 40_000_000


class CardImageError(GameError):
    pass


def cards_root():
    for source in (
        Path.cwd() / "assets/cards",
        Path(__file__).resolve().parents[3] / "assets/cards",
    ):
        if source.is_dir():
            return source
    return Path(str(files("cardlegends").joinpath("assets/cards")))


@dataclass(frozen=True)
class CardPNG:
    code: str
    content: bytes
    as_document: bool

    @property
    def cache_key(self):
        # Include bytes and media method; replacing a PNG never reuses stale file_id.
        digest = hashlib.sha256(b"fate-png-v1" + bytes([self.as_document]))
        digest.update(self.content)
        return digest.hexdigest()


def load_card_png(code, root=None):
    if code not in DROP_RATES:
        raise CardImageError("Неизвестный номер карточки.")
    path = (root or cards_root()) / f"{code}.png"
    hint = f"assets/cards/{code}.png"
    try:
        if not 0 < path.stat().st_size <= DOCUMENT_BYTES:
            raise CardImageError(f"Проверьте {hint}: PNG должен быть не больше 50 МБ.")
        # Verify directly from the file: do not hold a second PNG-sized buffer.
        # Read from the same descriptor, so an atomic operator replacement is safe.
        with path.open("rb") as stream:
            if not 0 < os.fstat(stream.fileno()).st_size <= DOCUMENT_BYTES:
                raise CardImageError(f"Проверьте размер PNG: {hint}.")
            image = Image.open(stream)
            try:
                width, height = image.size
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise CardImageError(f"Ожидается статический PNG: {hint}.")
                if width * height > MAX_PIXELS:
                    raise CardImageError(f"Слишком много пикселей в {hint}: максимум {MAX_PIXELS}.")
                image.verify()
                stream.seek(0)
                content = stream.read(DOCUMENT_BYTES + 1)
                if len(content) > DOCUMENT_BYTES:
                    raise CardImageError(f"Слишком большой PNG: {hint}.")
            finally:
                image.close()
    except FileNotFoundError:
        raise CardImageError(f"Карточка ещё не загружена. Добавьте оригинал {hint}.") from None
    except (
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        raise CardImageError(
            f"Не удалось прочитать PNG: {hint}. Проверьте исходный файл."
        ) from None
    return CardPNG(
        code,
        content,
        len(content) > PHOTO_BYTES
        or width + height > 10000
        or max(width, height) > min(width, height) * 20,
    )


def check_card_images(root=None):
    issues = []
    for code in DROP_RATES:
        try:
            load_card_png(code, root)
        except CardImageError as error:
            issues.append(str(error))
    return issues


def require_card_images(root=None):
    issues = check_card_images(root)
    if issues:
        raise CardImageError(
            "Призыв временно недоступен: изображения не готовы.\n" + "\n".join(issues)
        )
