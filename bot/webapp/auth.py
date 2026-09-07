"""Проверка подписи Telegram Mini App.

Мини-апп присылает initData — строку, подписанную ключом, производным от
токена бота. Без проверки подписи любой мог бы обратиться к API от чужого
имени и подать заявку на выплату на свой кошелёк, поэтому здесь ничего не
пропускается «на доверии»: id пользователя берётся только из проверенных
данных, никогда из тела запроса.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

logger = logging.getLogger(__name__)

# Сколько живёт подпись. Телефон может лежать в кармане с открытым апом,
# поэтому сутки — разумный предел: свежее ограничение мешало бы работать,
# а бессрочное позволяло бы переиспользовать перехваченную строку вечно.
MAX_AGE_SEC = 24 * 60 * 60


class AuthError(Exception):
    """initData не прошла проверку — запрос обслуживать нельзя."""


@dataclass(frozen=True)
class WebAppUser:
    user_id: int
    username: str | None
    full_name: str


def _secret_key(bot_token: str) -> bytes:
    """Ключ подписи по схеме Telegram: HMAC от токена со строкой WebAppData."""
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def validate_init_data(
    init_data: str, bot_token: str, max_age_sec: int = MAX_AGE_SEC
) -> WebAppUser:
    """Проверяет подпись и возвращает пользователя. Бросает AuthError.

    Порядок важен: сначала подпись, потом срок. Разбирать содержимое
    неподписанной строки бессмысленно — ей нельзя верить ни в одном поле.
    """
    if not init_data:
        raise AuthError("пустая initData")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        raise AuthError("в initData нет подписи")

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    expected = hmac.new(
        _secret_key(bot_token), check_string.encode(), hashlib.sha256
    ).hexdigest()

    # Сравнение постоянного времени: обычное == подсказывает подбирающему,
    # сколько символов он уже угадал.
    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("подпись не совпала")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        raise AuthError("испорченный auth_date") from None
    if auth_date <= 0:
        raise AuthError("нет auth_date")
    age = int(time.time()) - auth_date
    if age > max_age_sec:
        raise AuthError(f"подпись просрочена на {age - max_age_sec} секунд")

    raw_user = pairs.get("user", "")
    if not raw_user:
        raise AuthError("в initData нет пользователя")
    try:
        user = json.loads(raw_user)
    except json.JSONDecodeError:
        raise AuthError("пользователь в initData не разобрался") from None

    user_id = user.get("id")
    if not isinstance(user_id, int):
        raise AuthError("в initData нет числового id")

    name = " ".join(
        part for part in (user.get("first_name"), user.get("last_name")) if part
    )
    return WebAppUser(
        user_id=user_id,
        username=user.get("username") or None,
        full_name=name or user.get("username") or str(user_id),
    )
