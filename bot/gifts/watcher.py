"""Детект входящих NFT-подарков на личном аккаунте через Telethon.

Bot API не видит подарки, пришедшие на аккаунт человека, поэтому нужен userbot.
Ловим служебные сообщения MessageActionStarGift и MessageActionStarGiftUnique.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomingGift:
    slug: str
    gift_id: int
    title: str
    saved_id: int | None
    from_user_id: int | None      # None, если отправитель скрыт (name_hidden)
    can_resell_at: int            # unix-время выхода из кулдауна, 0 — без ограничений

    @property
    def is_attributed(self) -> bool:
        return self.from_user_id is not None


def parse_gift_action(action, sender_id: int | None) -> IncomingGift | None:
    """Разбирает служебное сообщение о подарке в IncomingGift.

    Возвращает None, если это не подарок или у него нет slug — без slug подарок
    невозможно сопоставить с позицией на MRKT.
    """
    gift = getattr(action, "gift", None)
    if gift is None:
        return None

    slug = getattr(gift, "slug", None)
    if not slug:
        # Обычный (не уникальный) подарок ещё не является NFT и не торгуется.
        return None

    hidden = bool(getattr(action, "name_hidden", False))
    from_id = None if hidden else sender_id

    return IncomingGift(
        slug=str(slug),
        gift_id=int(getattr(gift, "gift_id", 0) or getattr(gift, "id", 0) or 0),
        title=str(getattr(gift, "title", "") or ""),
        saved_id=getattr(action, "saved_id", None),
        from_user_id=from_id,
        can_resell_at=int(
            getattr(action, "can_resell_at", 0)
            or getattr(action, "can_transfer_at", 0)
            or 0
        ),
    )


class GiftWatcher:
    """Слушает аккаунт и отдаёт каждый новый подарок в колбэк."""

    def __init__(self, api_id: int, api_hash: str, session: str) -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        self._client = TelegramClient(StringSession(session), api_id, api_hash)

    @property
    def client(self):
        """Тот же MTProto-клиент используется для передачи подарков на MRKT."""
        return self._client

    async def connect(self) -> None:
        """Поднимает соединение до запуска слушателя — нужно депозитору."""
        if not self._client.is_connected():
            await self._client.start()

    async def start(self, on_gift: Callable[[IncomingGift], Awaitable[None]]) -> None:
        from telethon import events
        from telethon.tl import types

        actions = (types.MessageActionStarGift, types.MessageActionStarGiftUnique)

        @self._client.on(events.NewMessage(incoming=True))
        async def _handler(event) -> None:  # pragma: no cover — нужен живой аккаунт
            action = getattr(event.message, "action", None)
            if not isinstance(action, actions):
                return
            gift = parse_gift_action(action, event.sender_id)
            if gift is None:
                return
            logger.info(
                "Получен подарок %s от %s", gift.slug, gift.from_user_id or "скрытого отправителя"
            )
            await on_gift(gift)

        await self._client.start()
        me = await self._client.get_me()
        logger.info("Вотчер подарков запущен на аккаунте @%s", me.username or me.id)
        await self._client.run_until_disconnected()

    async def list_saved_gifts(self, limit: int = 100) -> list[IncomingGift]:
        """Перечисляет подарки, уже лежащие на аккаунте.

        Слушатель ловит только то, что приходит при работающем боте. Подарки,
        полученные до запуска или во время простоя, видны лишь так.
        """
        from telethon.tl import functions, types

        me = await self._client.get_input_entity("me")
        collected: list[IncomingGift] = []
        offset = ""

        while True:
            result = await self._client(
                functions.payments.GetSavedStarGiftsRequest(
                    peer=me, offset=offset, limit=limit
                )
            )
            for saved in result.gifts:
                sender_id = None
                from_id = getattr(saved, "from_id", None)
                if isinstance(from_id, types.PeerUser):
                    sender_id = from_id.user_id

                gift = parse_gift_action(saved, sender_id)
                if gift is not None:
                    collected.append(gift)

            offset = getattr(result, "next_offset", None) or ""
            if not offset or not result.gifts:
                break

        logger.info("На аккаунте найдено уникальных подарков: %s", len(collected))
        return collected

    async def stop(self) -> None:
        await self._client.disconnect()
