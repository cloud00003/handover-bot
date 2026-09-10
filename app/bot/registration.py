from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import exists, select

from app.bot import texts
from app.bot.authorization import RoleMiddleware
from app.db.models import Order, Role
from app.services.state_machine import TRANSITIONS
from app.services.authorization import AccessDenied, require_role
from app.services.registration import is_initialized, register_first_administrator

CONFIRM_ADMIN = "registration:confirm_admin"
MENU_ITEMS = (
    ("orders", texts.ORDERS), ("participants", texts.PARTICIPANTS),
    ("history", texts.HISTORY), ("settings", texts.SETTINGS),
)


def administrator_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"admin:{key}")]
        for key, label in MENU_ITEMS
    ])


async def start(message: Message, sessions, state: FSMContext) -> None:
    await state.clear()
    if not message.from_user or message.from_user.is_bot:
        await message.answer(texts.ACCESS_DENIED)
        return
    async with sessions() as session:
        initialized = await is_initialized(session)
        try:
            user = await require_role(session, message.from_user.id, *Role)
        except AccessDenied:
            user = None
        has_active_order = False
        if user is not None and user.role == Role.CUSTOMER:
            assignment = Order.courier_id if user.role == Role.COURIER else Order.customer_id
            has_active_order = bool(await session.scalar(select(exists().where(
                assignment == user.id,
                Order._status.in_([status for status, targets in TRANSITIONS.items() if targets]),
            ))))
    if user is not None and user.role == Role.ADMINISTRATOR:
        await message.answer(texts.ADMIN_MENU_GUIDANCE, reply_markup=administrator_menu())
    elif user is not None and user.role == Role.COURIER:
        from app.bot.courier import show_orders
        from app.bot.participants import participant_name

        welcome = texts.COURIER_GUIDANCE + "\n\n" + texts.PARTICIPANT_PROFILE.format(name=participant_name(user))
        await show_orders(message, sessions, state, welcome=welcome)
    elif user is not None:
        from app.bot.participants import participant_name

        guidance, empty = ((texts.COURIER_GUIDANCE, texts.COURIER_NO_ORDER)
                           if user.role == Role.COURIER
                           else (texts.CUSTOMER_GUIDANCE, texts.CUSTOMER_NO_ORDER))
        content = guidance + "\n\n" + texts.PARTICIPANT_PROFILE.format(name=participant_name(user))
        if not has_active_order:
            content += "\n\n" + empty
        await message.answer(content)
    elif not initialized:
        await message.answer(texts.WELCOME, reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CONFIRM_ADMIN, callback_data=CONFIRM_ADMIN)]]
        ))
    else:
        await message.answer(texts.ACCESS_DENIED)


async def confirm_administrator(query: CallbackQuery, sessions) -> None:
    if query.from_user.is_bot:
        await query.answer(texts.ACCESS_DENIED, show_alert=True)
        return
    # Commit before confirming success to Telegram.
    async with sessions.begin() as session:
        registered = await register_first_administrator(
            session, telegram_user_id=query.from_user.id,
            username=query.from_user.username, display_name=query.from_user.full_name,
        )
    if not registered:
        await query.answer(texts.ADMIN_REGISTRATION_CLOSED, show_alert=True)
        return
    await query.answer(texts.ADMIN_REGISTERED)
    if isinstance(query.message, Message):
        await query.message.edit_text(texts.ADMIN_MENU_GUIDANCE, reply_markup=administrator_menu())


async def menu_section(query: CallbackQuery) -> None:
    # Only the menu shell belongs to task 3. Section workflows are later tasks.
    await query.answer(texts.SECTION_UNAVAILABLE, show_alert=True)


def registration_router() -> Router:
    router = Router(name="registration")
    router.message.register(start, CommandStart())
    router.callback_query.register(confirm_administrator, F.data == CONFIRM_ADMIN)
    return router


def administrator_router() -> Router:
    router = Router(name="administrator")
    router.callback_query.filter(F.data.startswith("admin:"))
    router.callback_query.middleware(RoleMiddleware(Role.ADMINISTRATOR))
    router.callback_query.register(menu_section, F.data.in_({f"admin:{key}" for key, _ in MENU_ITEMS}))
    return router
