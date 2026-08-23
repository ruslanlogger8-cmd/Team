"""Хелперы отрисовки: безопасное редактирование и работа с состоянием."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


async def safe_edit(
    call: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Редактирует сообщение, гася безобидные отказы Telegram.

    'message is not modified' прилетает при повторном нажатии той же кнопки,
    'message to edit not found' — если сообщение удалили. Ни то, ни другое
    не должно выглядеть для пользователя как ошибка.
    """
    try:
        await call.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if "not modified" in message:
            return
        if "not found" in message or "can't be edited" in message:
            await call.message.answer(text, reply_markup=reply_markup)
            return
        raise


async def reset_state(state: FSMContext) -> None:
    """Сбрасывает FSM при переходе по меню.

    Без этого пользователь, начавший ввод кошелька и ушедший в другой раздел,
    остаётся в состоянии ожидания — и следующее его сообщение съедается
    обработчиком кошелька.
    """
    if await state.get_state() is not None:
        await state.clear()
