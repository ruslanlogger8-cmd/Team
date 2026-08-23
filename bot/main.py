"""Точка входа. aiogram 3.x polling.

Зависимости DI (db, config, payer) пробрасываются в хендлеры через
workflow_data диспетчера — доступны как аргументы по имени.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .config import Config
from .db import Database
from .emoji import configure as configure_emoji
from .handlers import build_router
from .premium_fallback import PremiumEmojiFallback
from .utils import fmt_ton
from .ton import create_payer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("payout-bot")


async def _set_commands(bot: Bot) -> None:
    """Список команд в меню Telegram."""
    await bot.set_my_commands([
        BotCommand(command="start", description="Меню"),
        BotCommand(command="id", description="Мой Telegram ID"),
        BotCommand(command="help", description="Как пользоваться"),
    ])


async def _report_stuck(bot: Bot, db: Database, config: Config) -> None:
    """Сообщает админам о заявках, зависших после падения процесса.

    Автоматически ничего не откатываем: транзакция могла уйти в сеть до падения,
    и слепой возврат означал бы двойную выплату.
    """
    stuck = await db.find_stuck_withdrawals()
    if not stuck:
        return

    logger.warning("Найдено зависших заявок: %s", len(stuck))
    lines = [
        f"#{wid} · {fmt_ton(amount)} · user {user_id}\n{wallet}"
        for wid, user_id, amount, wallet in stuck[:10]
    ]
    text = (
        f"\u26a0 <b>Зависшие заявки после перезапуска: {len(stuck)}</b>\n\n"
        + "\n\n".join(lines)
        + "\n\nПроверь адреса в блокчейне и закрой через "
        "<code>/resolve &lt;id&gt; sent|refund</code>"
    )
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001 — админ мог не запускать бота
            logger.warning("Не удалось уведомить админа %s о зависших заявках", admin_id)


async def _start_gifts(db: Database, config: Config, bot: Bot) -> list[asyncio.Task]:
    """Поднимает подсистему подарков, если она включена.

    Сбой здесь не должен ронять выплатной бот: он самодостаточен и работает
    без подарков.
    """
    if not config.gifts_enabled:
        logger.info("Подсистема подарков выключена (GIFTS_ENABLED=false)")
        return []

    try:
        from .gifts.market import Market
        from .gifts.poller import run_poller
        from .gifts.service import GiftService
        from .gifts.watcher import GiftWatcher
    except ImportError as exc:
        logger.error("Подарки не запущены, нет зависимостей: %s", exc)
        return []

    market = Market(config.tg_api_id, config.tg_api_hash, session_name="mrkt")
    await market.__aenter__()
    service = GiftService(db, config, market)

    watcher = GiftWatcher(config.tg_api_id, config.tg_api_hash, config.tg_session)

    async def on_gift(gift) -> None:
        outcome = await service.register(gift)
        logger.info("Подарок %s: %s", gift.slug, outcome)
        if outcome == "unattributed":
            for admin_id in config.admin_ids:
                try:
                    await bot.send_message(
                        admin_id,
                        f"Подарок <code>{gift.slug}</code> пришёл без опознания отправителя.\n"
                        f"Привяжи вручную: <code>/gift {gift.slug} ID_воркера</code>",
                    )
                except Exception:  # noqa: BLE001
                    pass

    tasks = [
        asyncio.create_task(watcher.start(on_gift), name="gift-watcher"),
        asyncio.create_task(run_poller(service, config, bot), name="gift-poller"),
    ]
    logger.info("Подсистема подарков запущена: доля воркера %s%%", config.worker_share_percent)
    return tasks


async def main() -> None:
    config = Config.load()
    configure_emoji(config.use_premium_emoji)

    db = Database(config.db_path)
    await db.connect()

    # Всё после connect() — под try/finally: иначе падение на старте оставляет
    # незакрытый поток aiosqlite, и процесс зависает вместо честного рестарта.
    bot: Bot | None = None
    try:
        payer = create_payer(config)
        logger.info("Горячий кошелёк: %s (testnet=%s)", payer.address, config.is_testnet)

        bot = Bot(
            token=config.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        bot.session.middleware(PremiumEmojiFallback())

        dp = Dispatcher(storage=MemoryStorage())
        dp.workflow_data.update(db=db, config=config, payer=payer)
        dp.include_router(build_router())

        await _set_commands(bot)
        await _report_stuck(bot, db, config)

        background = await _start_gifts(db, config, bot)

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Бот запущен, ожидаю сообщения")
        await dp.start_polling(bot)
    finally:
        for task in background:
            task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)
        await db.close()
        if bot is not None:
            await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено")
    except RuntimeError as exc:
        # Ошибки конфигурации: понятный текст вместо traceback на весь экран.
        logger.error("Не удалось запустить: %s", exc)
        raise SystemExit(1) from None
