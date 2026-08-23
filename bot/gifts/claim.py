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
    ATTACHED = "attached"           # привязали к этому воркеру
    ALREADY_YOURS = "already_yours"  # уже был за ним
    TAKEN = "taken"                 # закреплён за другим
    NOT_FOUND = "not_found"         # бот такого подарка не получал
    BAD_LINK = "bad_link"           # ссылку не разобрали


@dataclass(frozen=True)
class Claim:
    result: ClaimResult
    slug: str = ""
    title: str = ""
    status: str = ""

    @property
    def ok(self) -> bool:
        return self.result in (ClaimResult.ATTACHED, ClaimResult.ALREADY_YOURS)


async def submit_claim(db, worker_id: int, text: str) -> Claim:
    """Разбирает ссылку и привязывает подарок к воркеру, если это законно."""
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

    await db.attach_gift_worker(slug, worker_id)
    return Claim(ClaimResult.ATTACHED, slug, title, status)
