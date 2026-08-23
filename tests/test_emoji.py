"""Премиум-эмодзи: рендер, целостность карты и запрет обычных эмодзи в UI."""
import json
import pathlib
import re

import pytest

from bot import emoji
from bot.emoji import EMOJI, configure, e, esc, icon, premium_enabled, strip_premium

# Диапазоны, покрывающие пиктограммы и эмодзи-символы Unicode.
EMOJI_CHARS = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U00002B00-\U00002BFF]"
)
UI_FILES = [
    pathlib.Path("bot/keyboards.py"),
    pathlib.Path("bot/handlers/common.py"),
    pathlib.Path("bot/handlers/admin.py"),
]


@pytest.fixture(autouse=True)
def restore():
    configure(True)
    yield
    configure(True)


class TestRendering:
    def test_premium_mode_wraps_in_tag(self):
        configure(True)
        rendered = e("top")
        assert rendered.startswith('<tg-emoji emoji-id="')
        assert EMOJI["top"][0] in rendered

    def test_icon_returns_id(self):
        configure(True)
        assert icon("top") == EMOJI["top"][0]

    def test_disable_premium_switches_to_fallback(self):
        configure(True)
        assert premium_enabled() is True
        emoji.disable_premium()
        assert premium_enabled() is False
        assert "<tg-emoji" not in e("top")
        assert icon("top") is None

    def test_strip_premium_unwraps(self):
        configure(True)
        assert strip_premium(f"{e('coin')} 5 TON") == f"{EMOJI['coin'][1]} 5 TON"

    def test_unknown_key_is_safe(self):
        assert e("нет-такого") == "*"
        assert icon("нет-такого") is None


class TestData:
    def test_every_key_has_premium_id(self):
        for key, (custom_id, fallback_char) in EMOJI.items():
            assert custom_id.isdigit(), f"{key}: id не числовой"
            assert fallback_char, f"{key}: нет символа отката"

    def test_ids_exist_in_source_index(self):
        """ID обязаны быть из реального дампа паков, а не придуманы."""
        known = {r["id"] for r in json.loads(pathlib.Path("emoji_index.json").read_text("utf-8"))}
        for key, (custom_id, _) in EMOJI.items():
            assert custom_id in known, f"{key}: id {custom_id} нет в emoji_index.json"

    def test_no_duplicate_ids_for_different_meanings(self):
        seen: dict[str, str] = {}
        for key, (custom_id, _) in EMOJI.items():
            seen.setdefault(custom_id, key)


class TestNoPlainEmojiInUI:
    """Требование: в интерфейсе не должно быть ни одного обычного эмодзи."""

    @pytest.mark.parametrize("path", UI_FILES, ids=lambda p: p.name)
    def test_ui_source_has_no_literal_emoji(self, path):
        offenders = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            found = EMOJI_CHARS.findall(line)
            if found:
                offenders.append(f"{path}:{number} → {''.join(found)}")
        assert not offenders, "обычные эмодзи в интерфейсе:\n" + "\n".join(offenders)

    @pytest.mark.parametrize("path", UI_FILES, ids=lambda p: p.name)
    def test_ui_uses_emoji_helpers(self, path):
        source = path.read_text(encoding="utf-8")
        assert "e(" in source or "icon(" in source


class TestEscaping:
    @pytest.mark.parametrize("raw,expected", [
        ("<b>жирный</b>", "&lt;b&gt;жирный&lt;/b&gt;"),
        ("a & b", "a &amp; b"),
        ("Иван", "Иван"),
    ])
    def test_esc(self, raw, expected):
        assert esc(raw) == expected

    def test_injection_is_neutralised(self):
        assert "<tg-emoji" not in esc("<tg-emoji emoji-id='1'>x</tg-emoji>")
