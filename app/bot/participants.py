"""Administrator participant browsing and invitation creation."""

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.deep_linking import create_start_link
from sqlalchemy import select

from app.bot import texts
from app.bot.authorization import RoleMiddleware
from app.db.models import Role, User
from app.services.invitations import (
    InvalidInvitation, RoleConflict, create_invitation, redeem_invitation, validate_name,
)

ROLE_LABELS = {Role.COURIER: "Курьер", Role.CUSTOMER: "Заказчик"}


class ParticipantForm(StatesGroup):
    name = State()


def button(label, data):
    return InlineKeyboardButton(text=label, callback_data=data)


def participant_name(user: User) -> str:
    name = user.display_name or user.telegram_display_name or ROLE_LABELS[user.role]
    return f"{name} — {ROLE_LABELS[user.role]}"


async def list_participants(query: CallbackQuery, sessions, state: FSMContext):
    await state.clear()
    page = int(query.data.rsplit(":", 1)[-1]) if query.data.startswith("participants:page:") else 0
    async with sessions() as session:
        users = list((await session.scalars(select(User).where(
            User.active.is_(True), User.role.in_(ROLE_LABELS),
        ).order_by(User.id).offset(page * 10).limit(11))).all())
    rows = [[button(texts.ADD_COURIER, "participants:add:COURIER")],
            [button(texts.ADD_CUSTOMER, "participants:add:CUSTOMER")]]
    rows += [[button(participant_name(user), f"participants:view:{user.id}")] for user in users[:10]]
    navigation = []
    if page:
        navigation.append(button(texts.PREVIOUS, f"participants:page:{page - 1}"))
    if len(users) > 10:
        navigation.append(button(texts.NEXT, f"participants:page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([button(texts.ADMIN_MENU, "participants:menu")])
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.edit_text(texts.PARTICIPANTS if users else texts.NO_PARTICIPANTS,
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def show_participant(query: CallbackQuery, sessions):
    participant_id = int(query.data.rsplit(":", 1)[-1])
    async with sessions() as session:
        user = await session.scalar(select(User).where(
            User.id == participant_id, User.active.is_(True), User.role.in_(ROLE_LABELS),
        ))
    if user is None:
        await query.answer(texts.PARTICIPANT_UNAVAILABLE, show_alert=True)
        return
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.edit_text(participant_name(user), reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[button(texts.PARTICIPANTS, "admin:participants")]]))


async def add_participant(query: CallbackQuery, state: FSMContext):
    role = Role(query.data.rsplit(":", 1)[-1])
    await state.set_state(ParticipantForm.name)
    await state.set_data({"role": role.value})
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(texts.COURIER_NAME if role == Role.COURIER else texts.CUSTOMER_NAME,
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                       [button(texts.CANCEL, "participants:cancel")]]))


async def receive_name(message: Message, state: FSMContext, sessions):
    try:
        name = validate_name(message.text or "")
    except ValueError:
        await message.answer(texts.INVALID_PARTICIPANT_NAME)
        return
    data = await state.get_data()
    role = Role(data["role"])
    # Resolve the bot username before committing an invitation, so a getMe
    # failure cannot create a link that the administrator never sees.
    await message.bot.me()
    async with sessions.begin() as session:
        token = await create_invitation(session, administrator_id=message.from_user.id,
                                        role=role, name=name)
    link = await create_start_link(message.bot, f"invite_{token}")
    await state.clear()
    await message.answer(texts.INVITE_CREATED.format(name=name, role=ROLE_LABELS[role], link=link),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [button(texts.PARTICIPANTS, "admin:participants")]]))


async def cancel_form(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.answer(texts.ACTION_CANCELLED)
    if isinstance(query.message, Message):
        await query.message.edit_text(texts.ACTION_CANCELLED, reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[button(texts.PARTICIPANTS, "admin:participants")]]))


async def main_menu(query: CallbackQuery, state: FSMContext):
    from app.bot.registration import administrator_menu

    await state.clear()
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.edit_text(texts.ADMIN_MENU_RETURN, reply_markup=administrator_menu())


async def accept_invitation(message: Message, command: CommandObject, sessions, state: FSMContext):
    await state.clear()
    if not message.from_user or message.from_user.is_bot:
        await message.answer(texts.ACCESS_DENIED)
        return
    payload = command.args or ""
    if not payload.startswith("invite_"):
        await message.answer(texts.INVALID_INVITATION)
        return
    try:
        async with sessions.begin() as session:
            user = await redeem_invitation(session, token=payload[len("invite_"):],
                                           telegram_user_id=message.from_user.id,
                                           username=message.from_user.username,
                                           display_name=message.from_user.full_name)
    except InvalidInvitation:
        await message.answer(texts.INVALID_INVITATION)
        return
    except RoleConflict:
        await message.answer(texts.ROLE_CONFLICT)
        return
    await message.answer(texts.PARTICIPANT_REGISTERED.format(name=participant_name(user)))


def invitation_router() -> Router:
    router = Router(name="invitation_redemption")
    router.message.register(accept_invitation, CommandStart(deep_link=True))
    return router


def participants_router() -> Router:
    router = Router(name="participants")
    guard = RoleMiddleware(Role.ADMINISTRATOR)
    router.message.middleware(guard)
    router.callback_query.middleware(guard)
    router.callback_query.register(list_participants, F.data == "admin:participants")
    router.callback_query.register(list_participants, F.data.regexp(r"^participants:page:[0-9]{1,6}$"))
    router.callback_query.register(show_participant, F.data.regexp(r"^participants:view:[0-9]{1,18}$"))
    router.callback_query.register(add_participant, F.data.in_({"participants:add:COURIER", "participants:add:CUSTOMER"}))
    router.callback_query.register(cancel_form, F.data == "participants:cancel")
    router.callback_query.register(main_menu, F.data == "participants:menu")
    router.message.register(receive_name, ParticipantForm.name)
    return router
