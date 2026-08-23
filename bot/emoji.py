"""Премиум-эмодзи с автоматическим откатом на обычные.

Кастомные эмодзи в сообщениях бота показываются ТОЛЬКО если у аккаунта бота
есть Telegram Premium либо эмодзи взято из собственного набора бота. Если права
нет — Telegram отклонит сообщение, поэтому весь текст всегда имеет запасной
вариант на обычных эмодзи.

Как заполнить PREMIUM_IDS: перешли нужное премиум-эмодзи боту @idstickerbot,
он пришлёт custom_emoji_id. Пустое значение = используется обычный эмодзи.
"""
from __future__ import annotations

from html import escape

# Ключ → (custom_emoji_id, обычный эмодзи для отката).
# ID оставлены пустыми: подставь свои, иначе бот работает на обычных эмодзи.
EMOJI: dict[str, tuple[str, str]] = {
    "wave":    ("", "👋"),
    "user":    ("", "👤"),
    "money":   ("", "💰"),
    "coin":    ("", "🪙"),
    "wallet":  ("", "💼"),
    "send":    ("", "💸"),
    "check":   ("", "✅"),
    "cross":   ("", "❌"),
    "warn":    ("", "⚠️"),
    "time":    ("", "⏳"),
    "trophy":  ("", "🏆"),
    "chart":   ("", "📊"),
    "history": ("", "🧾"),
    "gear":    ("", "⚙️"),
    "back":    ("", "◀️"),
    "fire":    ("", "🔥"),
    "id":      ("", "🪪"),
}

_use_premium = False


def configure(use_premium: bool) -> None:
    """Включает попытку использовать премиум-эмодзи (флаг USE_PREMIUM_EMOJI)."""
    global _use_premium
    _use_premium = use_premium


def e(key: str) -> str:
    """Эмодзи для вставки в HTML-текст. Премиум, если задан id и включён флаг."""
    custom_id, fallback = EMOJI.get(key, ("", "•"))
    if _use_premium and custom_id:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback


def icon(key: str) -> str | None:
    """custom_emoji_id для иконки на кнопке, либо None."""
    custom_id, _ = EMOJI.get(key, ("", ""))
    return custom_id if (_use_premium and custom_id) else None


def strip_premium(text: str) -> str:
    """Убирает теги <tg-emoji>, оставляя обычные эмодзи. Для отката при ошибке API."""
    import re

    return re.sub(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', r"\1", text)


def esc(value: object) -> str:
    """Экранирование пользовательского текста для HTML."""
    return escape(str(value), quote=False)
