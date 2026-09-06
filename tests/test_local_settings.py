"""Вшитые в код настройки: подставляются, но не перебивают окружение."""
from __future__ import annotations

import os

import pytest

from bot.config import _apply_local_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("BOT_TOKEN", "ADMIN_IDS", "AUTO_PAYOUT"):
        monkeypatch.delenv(name, raising=False)


def test_env_wins_over_code(monkeypatch):
    """Иначе значение в Railway нельзя переопределить, не трогая код."""
    monkeypatch.setattr("bot.local_settings.SETTINGS", {"BOT_TOKEN": "из-кода"})
    monkeypatch.setenv("BOT_TOKEN", "из-окружения")

    _apply_local_settings()

    assert os.environ["BOT_TOKEN"] == "из-окружения"


def test_code_fills_the_gap(monkeypatch):
    monkeypatch.setattr("bot.local_settings.SETTINGS", {"BOT_TOKEN": "из-кода"})

    _apply_local_settings()

    assert os.environ["BOT_TOKEN"] == "из-кода"


def test_empty_value_is_not_set(monkeypatch):
    """Пустая строка означает «не задано», а не «задано пустым»."""
    monkeypatch.setattr("bot.local_settings.SETTINGS", {"ADMIN_IDS": "   "})

    _apply_local_settings()

    assert "ADMIN_IDS" not in os.environ


def test_values_are_stripped(monkeypatch):
    """Пробел, случайно скопированный вместе с токеном, ломает авторизацию."""
    monkeypatch.setattr("bot.local_settings.SETTINGS", {"BOT_TOKEN": "  токен \n"})

    _apply_local_settings()

    assert os.environ["BOT_TOKEN"] == "токен"


def test_missing_file_is_not_an_error(monkeypatch):
    """Без файла бот должен работать на одних переменных окружения."""
    import builtins

    real_import = builtins.__import__

    def fail_on_local_settings(name, *args, **kwargs):
        if name.endswith("local_settings"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_on_local_settings)
    _apply_local_settings()  # не должно бросить
