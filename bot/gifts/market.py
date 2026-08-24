"""Обёртка над amrkt: инвентарь, флоры, выставление, детект продажи.

Цены в API MRKT — нанотоны, как и внутри бота, конвертация не нужна.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InventoryItem:
    market_id: str                # идентификатор позиции внутри MRKT
    slug: str                     # имя подарка Telegram, ключ для нашей БД
    title: str
    collection: str
    model: str
    backdrop: str
    floor_backdrop_model_nano: int  # флор по связке «модель + фон» — главный ориентир
    floor_collection_nano: int      # флор по всей коллекции — занижает редкие атрибуты
    on_sale: bool
    locked: bool                    # залочен маркетом (кулдаун, вывод и т.п.)
    price_nano: int

    @property
    def sellable(self) -> bool:
        return not self.locked and not self.on_sale


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _str(value) -> str:
    return str(value) if value else ""


class Market:
    """Тонкий слой над MarketClient. Импорт ленивый: без GIFTS_ENABLED
    зависимость не нужна."""

    def __init__(
        self, api_id: int, api_hash: str, session_name: str, workdir: str = ".",
        proxy: str = "",
    ) -> None:
        from amrkt import MarketClient

        from .proxy import parse_proxy

        parse_proxy(proxy)  # ранняя проверка адреса: amrkt проглотит любой мусор
        self._client = MarketClient(
            api_id=api_id, api_hash=api_hash, session_name=session_name,
            workdir=workdir, proxy=proxy or None,
        )

    async def __aenter__(self) -> "Market":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.__aexit__(*exc)

    @staticmethod
    def to_item(gift) -> InventoryItem:
        """Приводит модель amrkt к нашей. Имена полей у MRKT менялись,
        поэтому slug ищем по нескольким вариантам."""
        slug = ""
        for attr in ("name", "slug", "gift_id_string", "gift_slug"):
            value = getattr(gift, attr, None)
            if isinstance(value, str) and value:
                slug = value
                break
        return InventoryItem(
            market_id=_str(getattr(gift, "id", "")),
            slug=slug,
            title=_str(getattr(gift, "title", None)),
            collection=_str(getattr(gift, "collection_name", None)),
            model=_str(getattr(gift, "model_name", None)),
            backdrop=_str(getattr(gift, "backdrop_name", None)),
            floor_backdrop_model_nano=_int(getattr(gift, "floor_price_by_backdrop_model", 0)),
            floor_collection_nano=_int(getattr(gift, "floor_price_by_collection", 0)),
            on_sale=bool(getattr(gift, "is_on_sale", False)),
            locked=bool(
                getattr(gift, "is_locked", False) or getattr(gift, "is_locked_for_sale", False)
            ),
            price_nano=_int(getattr(gift, "sale_price", 0)),
        )

    async def inventory(self) -> list[InventoryItem]:
        gifts = await self._client.get_inventory()
        return [self.to_item(g) for g in (gifts or [])]

    async def cheapest_comparable_nano(self, item: InventoryItem) -> int:
        """Запасной способ узнать флор: ищем самый дешёвый лот с той же
        моделью и фоном. Нужен, если MRKT не отдал floorPriceByBackdropModel.
        """
        if not item.collection:
            return 0
        try:
            found = await self._client.search_gifts(
                collection_names=[item.collection],
                model_names=[item.model] if item.model else None,
                backdrop_names=[item.backdrop] if item.backdrop else None,
                ordering="Price",
                low_to_high=True,
                count=5,
            )
        except Exception as exc:  # noqa: BLE001 — поиск не критичен
            logger.warning("Поиск сопоставимых лотов не удался: %s", exc)
            return 0

        gifts = getattr(found, "gifts", None) or getattr(found, "items", None) or []
        prices = [_int(getattr(g, "sale_price", 0)) for g in gifts]
        prices = [p for p in prices if p > 0]
        return min(prices) if prices else 0

    async def list_for_sale(self, market_id: str, price_nano: int) -> None:
        await self._client.sell_gifts([market_id], [price_nano])

    async def cancel(self, market_id: str) -> None:
        await self._client.cancel_sale([market_id])

    async def balance_nano(self) -> int:
        balance = await self._client.get_balance()
        return _int(getattr(balance, "hard", 0))
