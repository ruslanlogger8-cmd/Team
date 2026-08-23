"""Фоновый цикл: выставляет готовые подарки и собирает продажи."""
from __future__ import annotations

import asyncio
import logging

from ..config import Config
from ..emoji import e, esc
from ..utils import fmt_ton
from .service import GiftService

logger = logging.getLogger(__name__)
RULE = "━━━━━━━━━━━━━━━━━━━━"


async def run_poller(service: GiftService, config: Config, bot=None) -> None:
    """Крутится, пока жив процесс. Ошибка круга не должна его убивать."""
    logger.info("Опрос MRKT каждые %s сек", config.gifts_poll_sec)
    while True:
        try:
            for listing in await service.list_ready_gifts():
                logger.info(
                    "Выставлен %s за %s (флор %s, %s)",
                    listing.slug, listing.price_nano, listing.floor_nano, listing.source.value,
                )
                await notify(
                    bot, config,
                    f"{e('up')} <b>Подарок выставлен</b>\n"
                    f"{RULE}\n"
                    f"{e('gift')} {esc(listing.title)}\n"
                    f"{e('coin')} Цена · <b>{fmt_ton(listing.price_nano)}</b>\n"
                    f"{e('dot')} Флор {listing.source.value} · {fmt_ton(listing.floor_nano)}",
                )

            for sale in await service.collect_sales():
                logger.info(
                    "Продан %s за %s, доля %s", sale.slug, sale.sold_nano, sale.share_nano
                )
                await notify(
                    bot, config,
                    f"{e('check')} <b>Подарок продан</b>\n"
                    f"{RULE}\n"
                    f"{e('gift')} {esc(sale.title)}\n"
                    f"{e('coin')} Сумма · <b>{fmt_ton(sale.sold_nano)}</b>\n"
                    f"{e('withdraw')} Воркеру · <b>{fmt_ton(sale.share_nano)}</b>\n"
                    f"{e('balance')} Кассе · <b>{fmt_ton(sale.sold_nano - sale.share_nano)}</b>",
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — цикл обязан пережить любой сбой
            logger.exception("Ошибка цикла подарков")

        await asyncio.sleep(config.gifts_poll_sec)


async def notify(bot, config: Config, text: str) -> None:
    """Шлёт всем админам. Молчит, если бота нет (тесты) или админ недоступен."""
    if bot is None:
        return
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось уведомить админа %s", admin_id)
