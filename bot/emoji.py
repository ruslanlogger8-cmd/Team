"""Премиум-эмодзи. Обычные эмодзи в интерфейсе не используются.

Все иконки — кастомные премиум-эмодзи из паков PackIds, Finance и Restricted
(см. emoji_index.json). Пак Playerok не берём: это чужой бренд.
В тексте они вставляются тегом <tg-emoji>, на кнопках — полем
icon_custom_emoji_id.

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
    "logo":     ("5963312935148195483", "💎"),
    "profile":  ("5771887475421090729", "👤"),
    "balance":  ("5287231198098117669", "💰"),
    "wallet":   ("5445353829304387411", "💳"),
    "history":  ("5778605968208170641", "🕒"),
    "top":      ("5409008750893734809", "🏆"),
    "withdraw": ("5197434882321567830", "💵"),
    "admin":    ("5832546462478635761", "🔒"),
    "stats":    ("5877485980901971030", "📊"),
    "back":     ("5875082500023258804", "⬅️"),
    "next":     ("5877468380125990242", "➡️"),
    "check":    ("5776375003280838798", "✅"),
    "cross":    ("5872829476143894491", "🚫"),
    "warn":     ("5881702736843511327", "⚠️"),
    "time":     ("5778605968208170641", "🕒"),
    "wave":     ("5994750571041525522", "👋"),
    "fire":     ("6008118472066732010", "🔥"),
    "star":     ("5958376256788502078", "⭐️"),
    "gold":     ("5961051261204696786", "🥇"),
    "silver":   ("5283195573812340110", "🥈"),
    "bronze":   ("5282750778409233531", "🥉"),
    "medal":    ("5334644364280866007", "🏅"),
    "id":       ("5936017305585586269", "🪪"),
    "bell":     ("5909201569898827582", "🔔"),
    "shield":   ("5197288647275071607", "🛡"),
    "key":      ("6005570495603282482", "🔑"),
    "users":    ("5915556996215476302", "👥"),
    "gift":     ("6032937473162614352", "🎁"),
    "money":    ("5287231198098117669", "💰"),
    "coin":     ("5377505475015235101", "🪙"),
    "up":       ("5776219138917668486", "📈"),
    "dot":      ("5994324703559290598", "⚪️"),
    "link":     ("5877465816030515018", "🔗"),
    "clock":    ("5778605968208170641", "🕒"),
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
