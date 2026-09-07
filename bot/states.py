from aiogram.fsm.state import State, StatesGroup


class WalletForm(StatesGroup):
    waiting_address = State()


class CreditForm(StatesGroup):
    waiting_user = State()
    waiting_amount = State()


class ClaimForm(StatesGroup):
    waiting_link = State()
    waiting_username = State()
    waiting_photo = State()


class WithdrawForm(StatesGroup):
    waiting_amount = State()


class PayoutRequestForm(StatesGroup):
    """Заявка воркера: количество подарков, скриншот передачи и адрес."""

    waiting_count = State()
    waiting_photo = State()
    waiting_wallet = State()


class ApproveForm(StatesGroup):
    """Админ принял заявку и вводит сумму продажи."""

    waiting_sale = State()
