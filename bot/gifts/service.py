"""Оркестрация: подарок → выставление на MRKT → продажа → доля воркеру.

Состояния подарка:
    received → listed → sold → paid
                 ↘ skipped (нет привязки к воркеру или ошибка)

Начисление доли идёт через обычный db.credit, поэтому дальше работает та же
выплатная механика, что и у ручных начислений — включая автовыплату.
"""
from __future__ import annotations

import logging
import time

from ..config import Config
from ..db import Database
from .pricing import listing_price, worker_share
from .watcher import IncomingGift

logger = logging.getLogger(__name__)


class GiftService:
    def __init__(self, db: Database, config: Config, market=None) -> None:
        self._db = db
        self._config = config
        self._market = market

    async def register(self, gift: IncomingGift) -> str:
        """Записывает поступивший подарок. Возвращает исход для лога.

        Повторный slug игнорируется: иначе за один подарок заплатили бы дважды.
        """
        worker_id = gift.from_user_id
        if worker_id is not None and await self._db.get_worker(worker_id) is None:
            # Отправитель не зарегистрирован в боте — привяжет админ вручную.
            worker_id = None

        gift_row_id = await self._db.add_gift(
            slug=gift.slug,
            gift_id=gift.gift_id,
            title=gift.title,
            saved_id=gift.saved_id,
            worker_id=worker_id,
            can_resell_at=gift.can_resell_at,
        )
        if gift_row_id is None:
            return "duplicate"
        if worker_id is None:
            return "unattributed"
        return "registered"

    async def list_ready_gifts(self) -> list[tuple[str, int]]:
        """Выставляет на продажу подарки, вышедшие из кулдауна.

        Возвращает (slug, цена). Подарки без привязки к воркеру не выставляем:
        продав такой, мы не будем знать, кому платить.
        """
        if self._market is None:
            return []

        listed: list[tuple[str, int]] = []
        inventory = {item.slug: item for item in await self._market.inventory()}

        for row in await self._db.gifts_by_status("received", ready_only=True):
            slug = row["slug"]
            if row["worker_id"] is None:
                continue

            item = inventory.get(slug)
            if item is None:
                # Подарок ещё не доехал до MRKT — попробуем на следующем круге.
                continue

            floor = await self._market.floor_price_nano(row["title"] or item.title)
            price = listing_price(
                floor, self._config.undercut_percent, self._config.min_list_price_nano
            )
            try:
                await self._market.list_for_sale(item.gift_id, price)
            except Exception as exc:  # noqa: BLE001 — маркет мог отказать
                logger.warning("Не выставился %s: %s", slug, exc)
                continue

            await self._db.mark_gift_listed(slug, price)
            listed.append((slug, price))
        return listed

    async def collect_sales(self) -> list[tuple[str, int, int]]:
        """Находит проданные подарки и начисляет долю воркеру.

        Возвращает (slug, цена продажи, доля воркера).
        """
        if self._market is None:
            return []

        listed_rows = {row["slug"]: row for row in await self._db.gifts_by_status("listed")}
        if not listed_rows:
            return []

        results: list[tuple[str, int, int]] = []
        for slug, _ in await self._market.sold_since(set(listed_rows)):
            row = listed_rows[slug]
            # Ушёл по цене выставления: MRKT продаёт ровно по ней.
            sold_price = row["list_price_nano"]
            share = worker_share(sold_price, self._config.worker_share_percent)

            await self._db.mark_gift_sold(slug, sold_price, share)
            try:
                await self._db.credit(
                    row["worker_id"], share, 0, f"подарок {row['title'] or slug}"
                )
            except ValueError as exc:
                await self._db.mark_gift_skipped(slug, f"начисление не прошло: {exc}")
                logger.error("Доля за %s не начислена: %s", slug, exc)
                continue

            await self._db.mark_gift_paid(slug)
            results.append((slug, sold_price, share))
        return results

    async def pending_summary(self) -> dict[str, int]:
        stats = await self._db.gift_stats()
        now = int(time.time())
        waiting = [
            row for row in await self._db.gifts_by_status("received")
            if row["can_resell_at"] > now
        ]
        stats["in_cooldown"] = len(waiting)
        return stats
