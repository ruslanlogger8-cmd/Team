from aiogram.fsm.state import State, StatesGroup


class WalletForm(StatesGroup):
    waiting_address = State()


class CreditForm(StatesGroup):
    waiting_user = State()
    waiting_amount = State()


class ClaimForm(StatesGroup):
    waiting_link = State()


class WithdrawForm(StatesGroup):
    waiting_amount = State()
