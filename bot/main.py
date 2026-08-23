"""Точка входа. aiogram 3.x polling.

Зависимости DI (db, config, payer) пробрасываются в хендлеры через
workflow_data диспетчера — доступны как аргументы по имени.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .config import Config
from .db import Database
from .handlers import build_router
from .ton import TonPayer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("payout-bot")


async def main() -> None:
    config = Config.load()

    db = Database(config.db_path)
    await db.connect()

    payer = TonPayer(config)
    logger.info("Горячий кошелёк: %s (testnet=%s)", payer.address, config.is_testnet)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.workflow_data.update(db=db, config=config, payer=payer)
    dp.include_router(build_router())

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено")
