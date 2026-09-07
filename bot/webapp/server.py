"""HTTP-сервер мини-аппа: отдаёт страницу и обслуживает её запросы.

Крутится в том же процессе, что и бот, — база одна, и держать её в двух
процессах нельзя: SQLite с одним соединением этого не переживёт.

Все обработчики берут пользователя ТОЛЬКО из проверенной подписи. Тело
запроса приходит с телефона и может быть любым, поэтому оттуда читаются
лишь данные заявки, но никогда не то, от чьего имени она подана.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from ..config import Config
from ..db import Database
from ..utils import fmt_ton, is_valid_ton_address
from .auth import AuthError, WebAppUser, validate_init_data

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Столько подарков максимум в одной заявке — тот же предел, что и в боте.
MAX_GIFTS_PER_REQUEST = 50


def _user(request: web.Request) -> WebAppUser:
    """Пользователь из подписи. Заголовок или поле формы — что пришло."""
    init_data = request.headers.get("X-Init-Data", "")
    if not init_data:
        init_data = request.query.get("initData", "")
    try:
        return validate_init_data(init_data, request.app["config"].bot_token)
    except AuthError as exc:
        logger.info("Отклонён запрос мини-аппа: %s", exc)
        raise web.HTTPUnauthorized(
            text='{"error":"подпись не принята, открой апп заново"}',
            content_type="application/json",
        ) from None


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def api_me(request: web.Request) -> web.Response:
    user = _user(request)
    db: Database = request.app["db"]
    config: Config = request.app["config"]

    # Заход в апп — тоже знакомство: иначе воркер, не открывавший бота,
    # не появится в базе и не сможет подать заявку.
    await db.upsert_worker(user.user_id, user.username, user.full_name)
    worker = await db.get_worker(user.user_id)
    paid_total, paid_count = await db.worker_totals(user.user_id)
    open_request = await db.has_open_payout_request(user.user_id)

    return web.json_response({
        "team": config.team_name,
        "id": user.user_id,
        "name": worker.full_name if worker else user.full_name,
        "username": user.username,
        "wallet": worker.wallet if worker else None,
        "balance": fmt_ton(worker.balance_nano if worker else 0),
        "paid_total": fmt_ton(paid_total),
        "paid_count": paid_count,
        "share_percent": config.worker_share_percent,
        "has_open_request": open_request,
        "is_admin": user.user_id in config.admin_ids,
    })


async def api_top(request: web.Request) -> web.Response:
    _user(request)
    rows = await request.app["db"].get_top(10)
    return web.json_response({
        "top": [
            {"place": place, "name": name, "total": fmt_ton(total), "count": count}
            for place, (name, total, count) in enumerate(rows, 1)
        ]
    })


async def api_history(request: web.Request) -> web.Response:
    user = _user(request)
    db: Database = request.app["db"]
    rows = await db.get_withdrawals(user.user_id, page=1, per_page=20)
    return web.json_response({
        "history": [
            {
                "id": wid,
                "amount": fmt_ton(amount),
                "status": status,
                "tx_hash": tx_hash,
                "created_at": created,
            }
            for wid, amount, status, tx_hash, created in rows
        ]
    })


async def api_requests(request: web.Request) -> web.Response:
    """Открытые заявки самого воркера — чужие здесь не показываем."""
    user = _user(request)
    rows = await request.app["db"].pending_payout_requests()
    return web.json_response({
        "requests": [
            {
                "id": row["id"],
                "wallet": row["wallet"],
                "gifts_count": row["gifts_count"],
                "status": row["status"],
            }
            for row in rows if row["worker_id"] == user.user_id
        ]
    })


async def api_wallet(request: web.Request) -> web.Response:
    user = _user(request)
    body = await request.json()
    address = str(body.get("wallet", "")).strip()

    if not is_valid_ton_address(address):
        return web.json_response(
            {"error": "Адрес не прошёл проверку. Скопируй его целиком из кошелька."},
            status=400,
        )

    db: Database = request.app["db"]
    await db.upsert_worker(user.user_id, user.username, user.full_name)
    await db.set_wallet(user.user_id, address)
    return web.json_response({"ok": True, "wallet": address})


async def api_create_request(request: web.Request) -> web.Response:
    """Заявка на выплату: количество подарков, адрес и скриншот передачи."""
    user = _user(request)
    db: Database = request.app["db"]
    config: Config = request.app["config"]
    bot = request.app["bot"]

    fields: dict[str, str] = {}
    photo_bytes = b""
    photo_name = "screenshot.jpg"

    # Заявка приходит формой с файлом. Тело другого вида — не наш клиент,
    # и падать пятисоткой на нём незачем: отвечаем тем же, что и на форму
    # без фото.
    try:
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "photo":
                photo_name = part.filename or photo_name
                photo_bytes = await part.read(decode=False)
            else:
                fields[part.name] = (await part.text()).strip()
    except (AssertionError, ValueError, TypeError) as exc:
        logger.info("Заявка пришла не формой: %s", exc)
        return web.json_response(
            {"error": "Нужен скриншот передачи подарка."}, status=400
        )

    address = fields.get("wallet", "")
    if not is_valid_ton_address(address):
        return web.json_response(
            {"error": "Адрес не прошёл проверку. Скопируй его целиком из кошелька."},
            status=400,
        )

    raw_count = fields.get("gifts_count", "1")
    if not raw_count.isdigit() or not 1 <= int(raw_count) <= MAX_GIFTS_PER_REQUEST:
        return web.json_response(
            {"error": f"Количество подарков — число от 1 до {MAX_GIFTS_PER_REQUEST}."},
            status=400,
        )
    gifts_count = int(raw_count)

    if not photo_bytes:
        return web.json_response(
            {"error": "Нужен скриншот передачи подарка."}, status=400
        )

    await db.upsert_worker(user.user_id, user.username, user.full_name)
    if await db.has_open_payout_request(user.user_id):
        return web.json_response(
            {"error": "Одна заявка уже на рассмотрении. Дождись решения."},
            status=409,
        )

    # Сначала запись, потом показ: номер заявки должен быть настоящим, а не
    # угаданным заранее. Фото возвращается как file_id и дописывается следом,
    # чтобы /requests потом показал ту же картинку, не перезаливая её.
    request_id = await db.add_payout_request(user.user_id, address, None, gifts_count)
    file_id = await _send_to_admins(
        bot, config, user, address, gifts_count, photo_bytes, photo_name, request_id
    )
    if file_id:
        await db.set_payout_request_photo(request_id, file_id)

    logger.info("Заявка №%s из мини-аппа от %s", request_id, user.user_id)
    return web.json_response({"ok": True, "id": request_id})


async def _send_to_admins(
    bot, config: Config, user: WebAppUser, wallet: str, gifts_count: int,
    photo: bytes, filename: str, request_id: int,
) -> str | None:
    """Показывает заявку админам и возвращает file_id отправленного фото."""
    from aiogram.types import BufferedInputFile

    from ..emoji import e, esc
    from ..keyboards import request_decision

    who = f"@{user.username}" if user.username else str(user.user_id)
    caption = (
        f"{e('withdraw')} <b>Заявка №{request_id}</b>\n"
        f"{e('profile')} {esc(who)} · <code>{user.user_id}</code>\n"
        f"{e('gift')} Подарков · <b>{gifts_count}</b>\n"
        f"{e('wallet')} <code>{esc(wallet)}</code>"
    )

    file_id: str | None = None
    for admin_id in config.admin_ids:
        try:
            sent = await bot.send_photo(
                admin_id,
                BufferedInputFile(photo, filename=filename),
                caption=caption,
                reply_markup=request_decision(request_id),
            )
            if file_id is None and sent.photo:
                file_id = sent.photo[-1].file_id
        except Exception:  # noqa: BLE001 — админ мог не запускать бота
            logger.warning("Не удалось показать заявку админу %s", admin_id)
    return file_id


def build_app(db: Database, config: Config, bot) -> web.Application:
    app = web.Application(client_max_size=12 * 1024 * 1024)
    app["db"] = db
    app["config"] = config
    app["bot"] = bot

    app.router.add_get("/", index)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/top", api_top)
    app.router.add_get("/api/history", api_history)
    app.router.add_get("/api/requests", api_requests)
    app.router.add_post("/api/wallet", api_wallet)
    app.router.add_post("/api/request", api_create_request)
    app.router.add_static("/static/", STATIC_DIR)
    return app


async def run_webapp(db: Database, config: Config, bot) -> web.AppRunner:
    """Поднимает сервер и отдаёт runner — его нужно закрыть при остановке."""
    runner = web.AppRunner(build_app(db, config, bot), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.webapp_port)
    await site.start()
    logger.info("Мини-апп слушает порт %s", config.webapp_port)
    return runner
