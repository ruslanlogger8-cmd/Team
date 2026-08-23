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
# ID взяты из emoji_ids.json (паки Animated Emoji и Finance Emoji).
# Пустой id = всегда обычный эмодзи.
EMOJI: dict[str, tuple[str, str]] = {
    "wave":    ("5472055112702629499", "👋"),
    "user":    ("5373012449597335010", "👤"),
    "money":   ("5287231198098117669", "💰"),
    "coin":    ("5377505475015235101", "🪙"),
    "wallet":  ("5445221832074483553", "💼"),
    "send":    ("5472030678633684592", "💸"),
    "check":   ("5427009714745517609", "✅"),
    "cross":   ("5465665476971471368", "❌"),
    "warn":    ("5467928559664242360", "❗"),
    "time":    ("5451732530048802485", "⏳"),
    "trophy":  ("5409008750893734809", "🏆"),
    "chart":   ("5190806721286657692", "📊"),
    "history": ("5444856076954520455", "🧾"),
    "gear":    ("5449428597922079323", "🧰"),
    "back":    ("5469735272017043817", "👈"),
    "fire":    ("5420315771991497307", "🔥"),
    "id":      ("5445353829304387411", "💳"),
    "shield":  ("5197288647275071607", "🛡"),
    "star":    ("5267500801240092311", "⭐"),
    "up":      ("5197503331215361533", "📈"),
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


def fallback(key: str) -> str:
    """Обычный эмодзи для ключа — используется в тексте кнопок."""
    return EMOJI.get(key, ("", "•"))[1]


def disable_premium() -> None:
    """Навсегда выключает премиум-эмодзи в текущем процессе (после отказа Telegram)."""
    global _use_premium
    _use_premium = False


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
