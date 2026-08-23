"""Обёртка над amrkt: инвентарь, флор, выставление и детект продажи.

Все цены в API MRKT — нанотоны, как и внутри бота, поэтому конвертация не нужна.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InventoryItem:
    gift_id: str          # идентификатор внутри MRKT
    slug: str             # slug подарка Telegram — по нему матчим со своей БД
    title: str
    on_sale: bool
    price_nano: int


class Market:
    """Тонкий слой над MarketClient. Импорт ленивый: без GIFTS_ENABLED
    зависимость не нужна."""

    def __init__(self, api_id: int, api_hash: str, session_name: str, workdir: str = ".") -> None:
        from amrkt import MarketClient

        self._client = MarketClient(
            api_id=api_id, api_hash=api_hash, session_name=session_name, workdir=workdir
        )

    async def __aenter__(self) -> "Market":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.__aexit__(*exc)

    @staticmethod
    def _slug_of(item) -> str:
        """slug у MRKT может лежать в разных полях в зависимости от версии API."""
        for attr in ("slug", "name", "gift_slug", "telegram_slug"):
            value = getattr(item, attr, None)
            if isinstance(value, str) and value:
                return value
        return ""

    async def inventory(self) -> list[InventoryItem]:
        items = await self._client.get_inventory()
        result = []
        for item in items:
            result.append(
                InventoryItem(
                    gift_id=str(getattr(item, "id", "")),
                    slug=self._slug_of(item),
                    title=str(getattr(item, "title", "") or ""),
                    on_sale=bool(getattr(item, "on_sale", False) or getattr(item, "price", 0)),
                    price_nano=int(getattr(item, "price", 0) or 0),
                )
            )
        return result

    async def floor_price_nano(self, collection_title: str) -> int:
        """Минимальная цена по коллекции. 0 — если предложений нет."""
        try:
            found = await self._client.search_gifts(query=collection_title, count=20)
        except TypeError:
            found = await self._client.search_gifts(collection_title)
        prices = [
            int(getattr(g, "price", 0) or 0)
            for g in (found or [])
            if int(getattr(g, "price", 0) or 0) > 0
        ]
        return min(prices) if prices else 0

    async def list_for_sale(self, market_gift_id: str, price_nano: int) -> bool:
        result = await self._client.sell_gifts([market_gift_id], [price_nano])
        logger.info("Выставлен %s за %s нанотон: %s", market_gift_id, price_nano, result)
        return True

    async def cancel(self, market_gift_id: str) -> None:
        await self._client.cancel_sale([market_gift_id])

    async def balance_nano(self) -> int:
        balance = await self._client.get_balance()
        return int(getattr(balance, "hard", 0) or 0)

    async def sold_since(self, known_slugs: set[str]) -> list[tuple[str, int]]:
        """Подарки из known_slugs, которых больше нет в инвентаре — значит проданы.

        Возвращает (slug, цена выставления). Сверка по инвентарю надёжнее ленты
        активности: лента может отставать или обрезаться пагинацией.
        """
        present = {item.slug for item in await self.inventory() if item.slug}
        return [(slug, 0) for slug in known_slugs if slug not in present]
