import os
import tempfile

import pytest
import pytest_asyncio

from bot.db import Database
from bot.utils import build_ton_address


@pytest_asyncio.fixture
async def db():
    path = tempfile.mktemp(suffix=".db")
    database = Database(path)
    await database.connect()
    yield database
    await database.close()
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def wallet():
    return build_ton_address(os.urandom(32))
