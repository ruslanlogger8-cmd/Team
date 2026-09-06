"""Проверка токена на старте: причина должна быть в первой строке лога."""
from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError

from bot.main import _check_token


class FakeBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def get_me(self):
        if self.error:
            raise self.error
        return type("Me", (), {"username": "tonteam_bot", "id": 42})()


@pytest.mark.asyncio
async def test_frozen_bot_names_the_real_fix():
    """FROZEN_METHOD_INVALID — заморожен владелец, помогает только новый бот."""
    error = TelegramBadRequest(
        method=None, message="Bad Request: FROZEN_METHOD_INVALID"
    )
    with pytest.raises(RuntimeError) as err:
        await _check_token(FakeBot(error))

    text = str(err.value)
    assert "заморожен" in text.lower()
    assert "@BotFather" in text


@pytest.mark.asyncio
async def test_bad_token_names_the_variable():
    error = TelegramUnauthorizedError(method=None, message="Unauthorized")
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        await _check_token(FakeBot(error))


@pytest.mark.asyncio
async def test_other_bad_request_is_not_swallowed():
    """Чужую ошибку прятать нельзя — она про другую поломку."""
    error = TelegramBadRequest(method=None, message="Bad Request: chat not found")
    with pytest.raises(TelegramBadRequest):
        await _check_token(FakeBot(error))


@pytest.mark.asyncio
async def test_healthy_token_passes():
    await _check_token(FakeBot())
