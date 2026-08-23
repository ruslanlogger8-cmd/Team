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

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Бот запущен, ожидаю сообщения")
        await dp.start_polling(bot)
    finally:
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
