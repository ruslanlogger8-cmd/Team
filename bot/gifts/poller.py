"""Фоновый цикл: выставляет готовые подарки и собирает продажи."""
from __future__ import annotations

import asyncio
import logging

from ..config import Config
from ..db import Database
from ..emoji import e, esc
from ..payout import execute_payout
from ..utils import fmt_ton
from .service import GiftService

logger = logging.getLogger(__name__)
RULE = "━━━━━━━━━━━━━━━━━━━━"


async def run_poller(
    service: GiftService, config: Config, bot=None, db: Database | None = None,
    payer=None,
) -> None:
    """Крутится, пока жив процесс. Ошибка круга не должна его убивать."""
    logger.info("Опрос MRKT каждые %s сек", config.gifts_poll_sec)
    while True:
        try:
            for slug, outcome in await service.deposit_ready_gifts():
                if outcome == "deposited":
                    logger.info("Подарок %s передан на MRKT", slug)
                    await notify(
                        bot, config,
                        f"{e('next')} <b>Подарок отправлен на MRKT</b>\n"
                        f"{RULE}\n"
                        f"{e('dot')} <code>{esc(slug)}</code>\n"
                        f"{e('time')} Появится в инвентаре — выставим на продажу",
                    )
                else:
                    logger.warning("Подарок %s не передан: %s", slug, outcome)

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
                await _pay_share(bot, db, payer, config, sale)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — цикл обязан пережить любой сбой
            logger.exception("Ошибка цикла подарков")

        await asyncio.sleep(config.gifts_poll_sec)


async def notify(bot, config: Config, text: str, markup=None) -> None:
    """Шлёт всем админам. Молчит, если бота нет (тесты) или админ недоступен."""
    if bot is None:
        return
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось уведомить админа %s", admin_id)


async def _pay_share(bot, db, payer, config: Config, sale) -> None:
    """Отправляет долю воркеру сразу после продажи.

    Без этого доля просто оседала бы на балансе: автовыплата висела на команде
    начисления и продажу не подхватывала.
    """
    if db is None or payer is None:
        return

    if not config.auto_payout:
        # Ручной режим: админ выплачивает кнопкой, когда сочтёт нужным.
        from ..keyboards import pay_button

        await notify(
            bot, config,
            f"{e('withdraw')} <b>К выплате</b>\n"
            f"{RULE}\n"
            f"{e('profile')} Воркер · <code>{sale.worker_id}</code>\n"
            f"{e('coin')} Доля · <b>{fmt_ton(sale.share_nano)}</b>",
            markup=pay_button(sale.worker_id),
        )
        return

    result = await execute_payout(
        db, payer, sale.worker_id, config.min_withdraw_nano,
        max_single_nano=config.max_payout_nano,
        max_daily_nano=config.max_daily_payout_nano,
    )

    if result.status == "paid":
        logger.info("Доля за %s выплачена: %s", sale.slug, result.tx_hash)
        for target, text in (
            (sale.worker_id,
             f"{e('check')} <b>Выплата за подарок</b>\n"
             f"{RULE}\n"
             f"{e('gift')} {esc(sale.title)}\n"
             f"{e('coin')} <b>{fmt_ton(result.amount_nano)}</b>\n"
             f"{e('link')} <code>{esc(result.tx_hash)}</code>"),
        ):
            try:
                await bot.send_message(target, text)
            except Exception:  # noqa: BLE001
                pass
        return

    if result.status == "skipped":
        await notify(
            bot, config,
            f"{e('time')} Доля начислена, но выплата отложена · "
            f"воркер <code>{sale.worker_id}</code> не указал кошелёк "
            f"или сумма ниже минимума.",
        )
        return

    await notify(
        bot, config,
        f"{e('cross')} <b>Доля не выплачена</b>\n"
        f"{RULE}\n"
        f"{e('profile')} Воркер · <code>{sale.worker_id}</code>\n"
        f"{e('warn')} {esc(result.error)}\n"
        f"{e('check')} Средства остались на балансе.",
    )
