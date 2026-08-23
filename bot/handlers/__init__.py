from aiogram import Router

from . import common, admin


def build_router() -> Router:
    root = Router()
    root.include_router(admin.router)
    root.include_router(common.router)
    return root
