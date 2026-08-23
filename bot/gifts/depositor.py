"""Передача подарка на депозитный аккаунт MRKT.

Подарок не попадает на маркет сам: его нужно передать на аккаунт биржи
(по умолчанию @mrktbank), и только после этого он появляется в инвентаре
и его можно выставить на продажу.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Depositor:
    """Переводит подарки на аккаунт MRKT через MTProto."""

    def __init__(self, client, target: str) -> None:
        self._client = client
        self._target = target.lstrip("@")
        self._peer = None

    async def _resolve_target(self):
        if self._peer is None:
            self._peer = await self._client.get_input_entity(self._target)
            logger.info("Депозитный аккаунт MRKT: @%s", self._target)
        return self._peer

    async def deposit(self, slug: str) -> None:
        """Передаёт подарок на MRKT. Бросает исключение, если не вышло.

        Частые причины отказа: подарок ещё в кулдауне, уже передан,
        либо на аккаунте не хватает Stars на комиссию за передачу.
        """
        from telethon.tl import functions, types

        peer = await self._resolve_target()
        await self._client(
            functions.payments.TransferStarGiftRequest(
                stargift=types.InputSavedStarGiftSlug(slug=slug),
                to_id=peer,
            )
        )
        logger.info("Подарок %s передан на @%s", slug, self._target)
