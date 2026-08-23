"""Фоновый цикл: выставляет готовые подарки и собирает продажи."""
from __future__ import annotations

import asyncio
import logging

from ..config import Config
from ..utils import fmt_ton
from .service import GiftService

logger = logging.getLogger(__name__)


async def run_poller(service: GiftService, config: Config, bot=None) -> None:
    """Крутится, пока жив процесс. Ошибки цикла не должны его убивать."""
    logger.info("Опрос MRKT каждые %s сек", config.gifts_poll_sec)
    while True:
        try:
            for slug, price in await service.list_ready_gifts():
                logger.info("Выставлен %s за %s", slug, fmt_ton(price))

            for slug, sold, share in await service.collect_sales():
                logger.info("Продан %s за %s, доля %s", slug, fmt_ton(sold), fmt_ton(share))
                if bot is not None:
                    await _notify_admins(
                        bot, config,
                        f"Продан подарок {slug}\n"
                        f"Сумма {fmt_ton(sold)} · доля воркеру {fmt_ton(share)}",
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — цикл должен пережить любой сбой
            logger.exception("Ошибка цикла подарков")

        await asyncio.sleep(config.gifts_poll_sec)


async def _notify_admins(bot, config: Config, text: str) -> None:
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001
            pass
