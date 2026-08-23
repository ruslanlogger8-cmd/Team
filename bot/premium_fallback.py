"""Автооткат премиум-эмодзи.

Кастомные эмодзи показываются только если у аккаунта бота есть Telegram Premium.
Без него Telegram отклоняет каждое сообщение с <tg-emoji>, и бот выглядит
сломанным. Эта middleware ловит такой отказ, один раз выключает премиум-режим
и повторяет запрос уже с обычными эмодзи.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.methods.base import Response

from .emoji import disable_premium, strip_premium

logger = logging.getLogger(__name__)

_MARKERS = ("custom emoji", "CUSTOM_EMOJI", "premium", "EMOJI_INVALID")
_TEXT_FIELDS = ("text", "caption")


class PremiumEmojiFallback(BaseRequestMiddleware):
    def __init__(self) -> None:
        self._disabled = False

    async def __call__(
        self,
        make_request: Any,
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Response[Any]:
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            message = str(exc)
            if self._disabled or not any(m.lower() in message.lower() for m in _MARKERS):
                raise
            if not self._strip(method):
                raise

            self._disabled = True
            disable_premium()
            logger.warning(
                "Telegram отклонил премиум-эмодзи (%s). У аккаунта бота нет Premium — "
                "перехожу на обычные эмодзи до перезапуска.",
                message,
            )
            return await make_request(bot, method)

    @staticmethod
    def _strip(method: TelegramMethod[Any]) -> bool:
        """Убирает теги <tg-emoji> из текстовых полей. True, если что-то изменилось."""
        changed = False
        for field in _TEXT_FIELDS:
            value = getattr(method, field, None)
            if isinstance(value, str):
                cleaned = strip_premium(value)
                if cleaned != value:
                    object.__setattr__(method, field, cleaned)
                    changed = True
        return changed
