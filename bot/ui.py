"""Отрисовка экранов: безопасное редактирование и сброс состояния.

Меню может быть как обычным текстом, так и фото с подписью (MENU_PHOTO).
Telegram не даёт превратить текстовое сообщение в фото и наоборот, поэтому
редактирование само выбирает нужный метод по типу сообщения.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Telegram возвращает file_id после первой загрузки. Кэшируем его, чтобы
# не переотправлять картинку на каждое открытие меню.
_photo_cache: dict[str, str] = {}


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


def _resolve_photo(photo: str) -> str | FSInputFile:
    """MENU_PHOTO может быть file_id, ссылкой или путём к файлу в репозитории."""
    cached = _photo_cache.get(photo)
    if cached:
        return cached
    if photo.startswith(("http://", "https://")):
        return photo
    path = Path(photo)
    if path.is_file():
        return FSInputFile(path)
    return photo  # считаем, что это file_id


async def send_screen(
    message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo: str | None = None,
) -> None:
    """Отправляет экран: фото с подписью, если задано, иначе текст.

    Локальный файл загружается один раз, дальше используется file_id.
    Любая проблема с картинкой откатывает на текст — оформление не должно
    ронять бота.
    """
    if photo:
        try:
            sent = await message.answer_photo(
                _resolve_photo(photo), caption=text, reply_markup=reply_markup
            )
            if sent.photo:
                _photo_cache[photo] = sent.photo[-1].file_id
            return
        except (TelegramBadRequest, OSError, ValueError) as exc:
            logger.warning("MENU_PHOTO (%s) не отправился: %s — показываю текстом", photo, exc)
    await message.answer(text, reply_markup=reply_markup)


async def reset_state(state: FSMContext) -> None:
    """Сбрасывает FSM при переходе по меню.

    Без этого пользователь, начавший ввод кошелька и ушедший в другой раздел,
    остаётся в состоянии ожидания, и следующее сообщение съедается не тем
    обработчиком.
    """
    if await state.get_state() is not None:
        await state.clear()
