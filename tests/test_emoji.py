import json
import pathlib
import re

import pytest

from bot import emoji
from bot.emoji import EMOJI, configure, e, esc, fallback, icon, strip_premium


@pytest.fixture(autouse=True)
def restore_state():
    yield
    configure(False)


class TestRendering:
    def test_plain_mode_returns_bare_emoji(self):
        configure(False)
        assert e("money") == "💰"
        assert "<tg-emoji" not in e("trophy")

    def test_premium_mode_wraps_in_tag(self):
        configure(True)
        rendered = e("money")
        assert rendered.startswith("<tg-emoji emoji-id=")
        assert "💰" in rendered

    def test_unknown_key_does_not_crash(self):
        configure(True)
        assert e("нет-такого") == "•"
        assert icon("нет-такого") is None

    def test_icon_only_in_premium_mode(self):
        configure(False)
        assert icon("money") is None
        configure(True)
        assert icon("money") == EMOJI["money"][0]

    def test_strip_premium_restores_plain(self):
        configure(True)
        assert strip_premium(e("money") + " баланс") == "💰 баланс"

    def test_strip_premium_is_idempotent(self):
        configure(False)
        text = "💰 баланс"
        assert strip_premium(text) == text

    def test_disable_premium_takes_effect(self):
        configure(True)
        assert "<tg-emoji" in e("money")
        emoji.disable_premium()
        assert e("money") == "💰"


class TestData:
    def test_every_key_has_id_and_fallback(self):
        for key, (custom_id, plain) in EMOJI.items():
            assert custom_id.isdigit(), f"{key}: id не числовой"
            assert plain, f"{key}: нет запасного эмодзи"

    def test_ids_exist_in_source_json(self):
        """ID должны быть из реального дампа, а не выдуманы."""
        source = pathlib.Path("emoji_ids.json")
        known = {
            item["id"]
            for pack in json.loads(source.read_text(encoding="utf-8"))
            for item in pack["emojis"]
        }
        for key, (custom_id, _) in EMOJI.items():
            assert custom_id in known, f"{key}: id {custom_id} нет в emoji_ids.json"

    def test_fallback_matches_map(self):
        for key, (_, plain) in EMOJI.items():
            assert fallback(key) == plain


class TestEscaping:
    @pytest.mark.parametrize("raw,expected", [
        ("<b>жирный</b>", "&lt;b&gt;жирный&lt;/b&gt;"),
        ("a & b", "a &amp; b"),
        ("Иван", "Иван"),
        (123, "123"),
    ])
    def test_esc(self, raw, expected):
        assert esc(raw) == expected

    def test_injection_in_name_is_neutralised(self):
        """Имя из Telegram — недоверенный ввод, оно не должно ломать разметку."""
        assert "<tg-emoji" not in esc("<tg-emoji emoji-id='1'>x</tg-emoji>")
        assert not re.search(r"<[a-z]", esc("<script>alert(1)</script>"))
