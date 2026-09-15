"""Раздел «Все боты и Правила»: текст админа сохраняется один в один."""
from __future__ import annotations

import pytest

from bot.keyboards import info_menu, main_menu

INFO_KEY = "info"

# Как Telegram отдаёт разметку в html_text: цитата, жирный, премиум-эмодзи.
RICH = (
    '<tg-emoji emoji-id="5242287818499200693">💎</tg-emoji> <b>Наши боты</b>\n'
    "<blockquote>@tonnftteam_bot — выплаты\n"
    "@GarantHelperDeal — гарант</blockquote>\n"
    '<a href="https://t.me/+9cYsWpZxjScxZWNi">Чат команды</a>'
)


@pytest.mark.asyncio
async def test_text_is_stored_byte_for_byte(db):
    """Любая переделка разметки вернёт воркерам не то, что набрал админ."""
    await db.set_setting(INFO_KEY, RICH, None)

    assert (await db.get_setting(INFO_KEY))["value"] == RICH


@pytest.mark.asyncio
async def test_photo_is_stored_with_the_text(db):
    await db.set_setting(INFO_KEY, RICH, "photo-42")

    saved = await db.get_setting(INFO_KEY)
    assert saved["photo_id"] == "photo-42"
    assert saved["value"] == RICH


@pytest.mark.asyncio
async def test_saving_again_replaces_the_previous(db):
    await db.set_setting(INFO_KEY, "старое", "photo-1")
    await db.set_setting(INFO_KEY, "новое", "photo-2")

    saved = await db.get_setting(INFO_KEY)
    assert (saved["value"], saved["photo_id"]) == ("новое", "photo-2")


@pytest.mark.asyncio
async def test_photo_can_be_dropped_on_rewrite(db):
    """Прислал один текст без фото — картинка тоже должна уйти."""
    await db.set_setting(INFO_KEY, "с фото", "photo-1")
    await db.set_setting(INFO_KEY, "без фото", None)

    assert (await db.get_setting(INFO_KEY))["photo_id"] is None


@pytest.mark.asyncio
async def test_empty_section_reads_as_absent(db):
    assert await db.get_setting(INFO_KEY) is None


def test_menu_shows_the_section_to_everyone():
    texts = [b.text for row in main_menu().inline_keyboard for b in row]
    assert "Все боты и Правила" in texts


def test_edit_button_is_admin_only():
    """Воркер не должен даже видеть кнопку правки."""
    worker = [b.callback_data for row in info_menu().inline_keyboard for b in row]
    admin = [b.callback_data for row in info_menu(True).inline_keyboard for b in row]

    assert "info:edit" not in worker
    assert "info:edit" in admin
