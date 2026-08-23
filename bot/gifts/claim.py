"""Заявка воркера на подарок: разбор ссылки и проверка.

Заявка не начисляет деньги сама по себе — она лишь привязывает подарок,
который бот УЖЕ принял, к конкретному воркеру. Заявить то, чего не приходило,
невозможно: проверка идёт по своей базе, а не по словам воркера.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# t.me/nft/PlushPepe-42, https://t.me/nft/PlushPepe-42, @PlushPepe-42, PlushPepe-42
_LINK = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/nft/([A-Za-z0-9_\-]+)", re.IGNORECASE
)
_BARE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]*-\d+)$")


def parse_nft_slug(text: str) -> str | None:
    """Достаёт slug подарка из ссылки или голого имени.

    Возвращает None, если распознать не удалось — просить переслать ссылку
    надёжнее, чем гадать.
    """
    if not text:
        return None
    cleaned = text.strip()

    match = _LINK.search(cleaned)
    if match:
        return match.group(1)

    match = _BARE.match(cleaned)
    if match:
        return match.group(1)
    return None


class ClaimResult(str, Enum):
    ATTACHED = "attached"            # привязали сразу
    PENDING = "pending"              # ушло админу на подтверждение
    DUPLICATE = "duplicate"          # на этот подарок уже есть заявка
    ALREADY_YOURS = "already_yours"  # уже был за ним
    TAKEN = "taken"                  # закреплён за другим
    NOT_FOUND = "not_found"          # бот такого подарка не получал
    BAD_LINK = "bad_link"            # ссылку не разобрали


@dataclass(frozen=True)
class Claim:
    result: ClaimResult
    slug: str = ""
    title: str = ""
    status: str = ""
    request_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.result in (
            ClaimResult.ATTACHED, ClaimResult.ALREADY_YOURS, ClaimResult.PENDING
        )


async def submit_claim(db, worker_id: int, text: str, needs_approval: bool = True) -> Claim:
    """Разбирает ссылку и оформляет притязание воркера на подарок.

    Слаг подарка публичный — он виден в ссылке любому. Поэтому свободный
    подарок НЕ отдаётся первому попросившему: заявка уходит админу на
    подтверждение. Иначе чужой подарок со скрытым отправителем мог бы забрать
    кто угодно, кто увидел ссылку.
    """
    slug = parse_nft_slug(text)
    if slug is None:
        return Claim(ClaimResult.BAD_LINK)

    gift = await db.get_gift(slug)
    if gift is None:
        return Claim(ClaimResult.NOT_FOUND, slug=slug)

    owner = gift["worker_id"]
    title = gift["title"] or slug
    status = gift["status"]

    if owner == worker_id:
        return Claim(ClaimResult.ALREADY_YOURS, slug, title, status)
    if owner is not None:
        # Подарок уже закреплён за другим — молча переписывать нельзя,
        # иначе выплату получит не тот, кто прислал.
        return Claim(ClaimResult.TAKEN, slug, title, status)

    if not needs_approval:
        # Занимаем условным UPDATE: между проверкой и записью не должно быть
        # зазора, иначе две одновременные заявки пройдут обе.
        won = await db.claim_gift_if_free(slug, worker_id)
        if not won:
            return Claim(ClaimResult.TAKEN, slug, title, status)
        return Claim(ClaimResult.ATTACHED, slug, title, status)

    request_id = await db.add_claim_request(slug, worker_id)
    if request_id is None:
        return Claim(ClaimResult.DUPLICATE, slug, title, status)
    return Claim(ClaimResult.PENDING, slug, title, status, request_id)
