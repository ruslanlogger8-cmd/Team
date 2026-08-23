"""Восстановление файла сессии MRKT из переменной окружения.

amrkt работает поверх pyrogram/kurigram, а тот хранит авторизацию в файле
.session и, не найдя его, спрашивает телефон в консоли. На сервере консоли
нет — процесс падает с EOFError.

Решение: сессия создаётся один раз локально, файл кладётся в MRKT_SESSION_B64
в base64, и при старте бот восстанавливает его на диск.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

MRKT_SESSION_NAME = "mrkt"


def restore_mrkt_session(config) -> None:
    """Пишет файл сессии из MRKT_SESSION_B64, если он ещё не на диске.

    Существующий файл не трогаем: pyrogram обновляет его в процессе работы,
    и перезапись откатила бы состояние.
    """
    workdir = Path(getattr(config, "mrkt_workdir", ".") or ".")
    target = workdir / f"{MRKT_SESSION_NAME}.session"

    if target.exists() and target.stat().st_size > 0:
        logger.info("Сессия MRKT найдена: %s", target)
        return

    encoded = os.environ.get("MRKT_SESSION_B64", "").strip()
    if not encoded:
        raise RuntimeError(
            "нет файла сессии MRKT и не задана MRKT_SESSION_B64. "
            "Сгенерируй сессию локально: python scripts/gen_mrkt_session.py"
        )

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"MRKT_SESSION_B64 не разбирается как base64: {exc}") from None

    if not raw:
        raise RuntimeError("MRKT_SESSION_B64 пустая после декодирования")

    # Файл сессии — SQLite, в base64 он вылезает за лимит переменной в Railway
    # (32768 символов), поэтому его сжимают. Принимаем оба вида: по сигнатуре
    # gzip видно, надо ли распаковывать.
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise RuntimeError(f"MRKT_SESSION_B64 не распаковывается: {exc}") from None
        logger.info("Сессия MRKT распакована из gzip")

    workdir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    target.chmod(0o600)
    logger.info("Сессия MRKT восстановлена из MRKT_SESSION_B64 в %s", target)
