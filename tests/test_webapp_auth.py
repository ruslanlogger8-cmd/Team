"""Подпись мини-аппа. Пропустить чужую — отдать выплаты кому угодно."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from bot.webapp.auth import AuthError, validate_init_data

TOKEN = "8809440322:AAFtest-token-for-tests-only"


def make_init_data(token: str = TOKEN, age_sec: int = 0, **overrides) -> str:
    user = {"id": 974288213, "first_name": "Garant", "last_name": "Менеджер",
            "username": "garant"}
    pairs = {
        "auth_date": str(int(time.time()) - age_sec),
        "query_id": "AAE",
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
    }
    pairs.update({k: v for k, v in overrides.items() if v is not None})
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_valid_signature_gives_the_user():
    user = validate_init_data(make_init_data(), TOKEN)

    assert user.user_id == 974288213
    assert user.username == "garant"
    assert user.full_name == "Garant Менеджер"


def test_signature_from_another_bot_is_refused():
    """Токен чужого бота — чужая подпись, доверять ей нельзя."""
    with pytest.raises(AuthError, match="подпись"):
        validate_init_data(make_init_data(token="000:другой"), TOKEN)


def test_tampered_user_is_refused():
    """Главная атака: подменить id и получить чужие выплаты."""
    data = make_init_data()
    forged = data.replace("974288213", "111111111")

    with pytest.raises(AuthError, match="подпись"):
        validate_init_data(forged, TOKEN)


def test_missing_hash_is_refused():
    pairs = {"auth_date": str(int(time.time())), "user": "{}"}
    with pytest.raises(AuthError, match="нет подписи"):
        validate_init_data(urlencode(pairs), TOKEN)


def test_empty_init_data_is_refused():
    with pytest.raises(AuthError):
        validate_init_data("", TOKEN)


def test_stale_signature_is_refused():
    """Перехваченную строку нельзя переиспользовать вечно."""
    with pytest.raises(AuthError, match="просрочена"):
        validate_init_data(make_init_data(age_sec=48 * 3600), TOKEN)


def test_fresh_signature_within_the_window_passes():
    assert validate_init_data(make_init_data(age_sec=3600), TOKEN).user_id == 974288213


def test_user_without_id_is_refused():
    data = make_init_data(user=json.dumps({"first_name": "Кто-то"}))
    with pytest.raises(AuthError, match="id"):
        validate_init_data(data, TOKEN)


def test_name_falls_back_to_username():
    data = make_init_data(
        user=json.dumps({"id": 5, "username": "solo"}, separators=(",", ":"))
    )
    assert validate_init_data(data, TOKEN).full_name == "solo"
