"""Премиум-эмодзи. Обычные эмодзи в интерфейсе не используются.

Все иконки — кастомные премиум-эмодзи из паков Playerok Icons, Finance и
Animated (см. emoji_index.json). В тексте они вставляются тегом <tg-emoji>,
на кнопках — полем icon_custom_emoji_id.

ВАЖНО: Telegram показывает кастомные эмодзи, только если у аккаунта БОТА есть
Telegram Premium. Без него сервер отклоняет сообщение целиком, поэтому в
premium_fallback.py есть аварийный откат — он не даёт боту молча сломаться,
но при этом громко пишет в лог и предупреждает админа, что Premium не подключён.
"""
from __future__ import annotations

import re
from html import escape

# Ключ → (custom_emoji_id, символ-заглушка для аварийного отката).
# Заглушка используется ТОЛЬКО если у бота нет Premium и Telegram отклонил
# сообщение. В нормальной работе виден исключительно премиум-эмодзи.
EMOJI: dict[str, tuple[str, str]] = {
    "logo":      ("5242287818499200693", "💎"),
    "profile":   ("5771887475421090729", "👤"),
    "balance":   ("5415673019718714238", "💰"),
    "wallet":    ("5265245148840745641", "💳"),
    "history":   ("5415959764620299563", "🕒"),
    "top":       ("5435970558418242653", "🏆"),
    "withdraw":  ("5415924193701153272", "💵"),
    "admin":     ("5413721249140461044", "🔒"),
    "stats":     ("5877485980901971030", "📊"),
    "back":      ("5244626445371724507", "⬅️"),
    "next":      ("5438575460378233766", "➡️"),
    "check":     ("5413721442413988676", "✅"),
    "cross":     ("5413780811746921402", "🚫"),
    "warn":      ("5188387172935290178", "⚠️"),
    "time":      ("5415959764620299563", "🕒"),
    "wave":      ("5413743806308698813", "👋"),
    "fire":      ("5413639056351315601", "🔥"),
    "star":      ("5467515585673842012", "⭐️"),
    "gold":      ("5415704106692009668", "🥇"),
    "silver":    ("5415866233117493605", "🥈"),
    "bronze":    ("5416077760256821038", "🥉"),
    "medal":     ("5301282681823188202", "🏅"),
    "id":        ("5936017305585586269", "🪪"),
    "bell":      ("5413565058359774812", "🔔"),
    "shield":    ("5413586872498670501", "🛡"),
    "key":       ("5436101426071753223", "🔑"),
    "users":     ("5915556996215476302", "👥"),
    "gift":      ("5415794154976330480", "🎁"),
    "money":     ("5415673019718714238", "💰"),
    "coin":      ("5469813019515050486", "🪙"),
    "up":        ("5776219138917668486", "📈"),
    "dot":       ("5449648985578945152", "🔵"),
    "link":      ("5877465816030515018", "🔗"),
    "clock":     ("5415959764620299563", "🕒"),
}

_use_premium = True
_TAG = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>')


def configure(use_premium: bool) -> None:
    global _use_premium
    _use_premium = use_premium


def disable_premium() -> None:
    """Аварийное отключение после отказа Telegram (у бота нет Premium)."""
    global _use_premium
    _use_premium = False


def premium_enabled() -> bool:
    return _use_premium


def e(key: str) -> str:
    """Премиум-эмодзи для вставки в HTML-текст сообщения."""
    custom_id, fallback_char = EMOJI.get(key, ("", "*"))
    if not custom_id:
        return fallback_char
    if _use_premium:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback_char}</tg-emoji>'
    return fallback_char


def icon(key: str) -> str | None:
    """custom_emoji_id для иконки на кнопке."""
    custom_id, _ = EMOJI.get(key, ("", ""))
    return custom_id if (_use_premium and custom_id) else None


def strip_premium(text: str) -> str:
    """Разворачивает теги <tg-emoji> обратно в символы — для аварийного отката."""
    return _TAG.sub(r"\1", text)


def esc(value: object) -> str:
    """Экранирование недоверенного текста (имена, ошибки) для HTML."""
    return escape(str(value), quote=False)
