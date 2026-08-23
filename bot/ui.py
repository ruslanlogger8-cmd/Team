"""Отрисовка экранов: безопасное редактирование и сброс состояния.

Меню может быть как обычным текстом, так и фото с подписью (MENU_PHOTO).
Telegram не даёт превратить текстовое сообщение в фото и наоборот, поэтому
редактирование само выбирает нужный метод по типу сообщения.
"""
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
    """Обновляет экран, гася безобидные отказы Telegram.

    'message is not modified' прилетает при повторном нажатии той же кнопки,
    'message to edit not found' — если сообщение удалили. Пользователь не
    должен видеть в этом ошибку.
    """
    message = call.message
    try:
        if message.photo:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
        else:
            await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        reason = str(exc).lower()
        if "not modified" in reason:
            return
        if "not found" in reason or "can't be edited" in reason or "no text" in reason:
            await message.answer(text, reply_markup=reply_markup)
            return
        raise


async def send_screen(
    message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo: str | None = None,
) -> None:
    """Отправляет экран: фото с подписью, если задано, иначе текст.

    При неверном file_id или недоступной ссылке молча уходит в текст —
    оформление не должно ронять бота.
    """
    if photo:
        try:
            await message.answer_photo(photo, caption=text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            logger.warning("Не удалось отправить MENU_PHOTO (%s) — показываю текстом", exc)
    await message.answer(text, reply_markup=reply_markup)


async def reset_state(state: FSMContext) -> None:
    """Сбрасывает FSM при переходе по меню.

    Без этого пользователь, начавший ввод кошелька и ушедший в другой раздел,
    остаётся в состоянии ожидания, и следующее сообщение съедается не тем
    обработчиком.
    """
    if await state.get_state() is not None:
        await state.clear()
