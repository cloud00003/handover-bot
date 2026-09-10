"""Administrator settings and in-memory order drafts; no order lifecycle actions."""

import secrets

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.bot import order_texts as copy, texts
from app.bot.authorization import RoleMiddleware
from app.bot.registration import administrator_menu
from app.db.models import Role, User
from app.services.order_creation import (
    DEFAULT_FIELDS, ORDER_FIELDS, PARTICIPANT_FIELDS, InvalidDraft, create_order,
    participant, read_defaults, save_default, validate_draft, validate_field,
)


class Form(StatesGroup):
    input = State()
    summary = State()
    edit = State()


def markup(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows
    ])


def named(user):
    return user.display_name or user.telegram_display_name or copy.LABELS[
        "courier_id" if user.role == Role.COURIER else "customer_id"]


async def render(event, content, rows):
    if isinstance(event, CallbackQuery):
        if isinstance(event.message, Message):
            await event.message.edit_text(content, reply_markup=markup(rows))
    else:
        await event.answer(content, reply_markup=markup(rows))


async def display_values(session, values):
    result = dict(values)
    for field in PARTICIPANT_FIELDS:
        try:
            result[field] = named(await participant(session, field, values.get(field)))
        except InvalidDraft:
            result[field] = copy.NOT_SET
    return result


async def settings_screen(event, sessions, state):
    await state.clear()
    async with sessions() as session:
        values = await display_values(session, await read_defaults(session))
    content = texts.SETTINGS + "\n\n" + "\n".join(
        f"{copy.LABELS[field]}: {values.get(field) or copy.NOT_SET}" for field in DEFAULT_FIELDS)
    await render(event, content, [[(copy.LABELS[field], f"ops:set:{field}")] for field in DEFAULT_FIELDS]
                 + [[(texts.ADMIN_MENU, "ops:menu")]])


async def orders_screen(event, state, sessions):
    from app.bot.admin_orders import show_list

    await show_list(event, sessions, state)


async def prompt(event, sessions, state, field, page=0):
    data = await state.get_data()
    nonce = secrets.token_hex(6)
    await state.set_state(Form.input)
    await state.update_data(field=field, nonce=nonce)
    value = data["draft"].get(field)
    content = copy.PROMPTS[field]
    rows = []
    if field in PARTICIPANT_FIELDS:
        async with sessions() as session:
            users = list((await session.scalars(select(User).where(
                User.active.is_(True), User.role == PARTICIPANT_FIELDS[field],
            ).order_by(User.id).offset(page * 10).limit(11))).all())
            try:
                current = await participant(session, field, value)
                content += "\n\nТекущее значение: " + named(current)
                rows.append([(copy.KEEP, f"form:{nonce}:keep")])
            except InvalidDraft:
                pass
        rows += [[(named(user), f"form:{nonce}:pick:{user.id}")] for user in users[:10]]
        if not users:
            content += "\n\n" + copy.NO_CHOICES
        nav = []
        if page:
            nav.append((texts.PREVIOUS, f"form:{nonce}:page:{page - 1}"))
        if len(users) > 10:
            nav.append((texts.NEXT, f"form:{nonce}:page:{page + 1}"))
        if nav:
            rows.append(nav)
    elif value is not None:
        content += "\n\nТекущее значение: " + str(value)
        rows.append([(copy.KEEP, f"form:{nonce}:keep")])
    if field == "product_description":
        rows.append([(copy.SKIP, f"form:{nonce}:skip")])
    rows.append([(texts.CANCEL, f"form:{nonce}:cancel")])
    await render(event, content, rows)


async def summary(event, sessions, state):
    data = await state.get_data()
    try:
        async with sessions() as session:
            await validate_draft(session, data["draft"])
            values = await display_values(session, data["draft"])
    except InvalidDraft as exc:
        await state.update_data(editing=True)
        await prompt(event, sessions, state, str(exc))
        return
    nonce = secrets.token_hex(6)
    await state.set_state(Form.summary)
    await state.update_data(nonce=nonce)
    content = copy.SUMMARY + "\n\n" + "\n".join(
        f"{copy.LABELS[field]}: {values.get(field) or 'Не указано'}" for field in ORDER_FIELDS)
    content += "\n\nДата и время указаны по Бишкеку."
    await render(event, content, [[(copy.CREATE, f"form:{nonce}:confirm")],
                                 [(copy.EDIT, f"form:{nonce}:edit")],
                                 [(texts.CANCEL, f"form:{nonce}:cancel")]])


async def accept_value(event, sessions, state, value):
    data = await state.get_data()
    field = data["field"]
    try:
        if not (field == "product_description" and value is None):
            value = validate_field(field, value)
        async with sessions.begin() as session:
            if field in PARTICIPANT_FIELDS:
                await participant(session, field, value)
            if data["mode"] == "settings":
                await save_default(session, event.from_user.id, field, value)
    except InvalidDraft:
        error = copy.INVALID_PARTICIPANT if field in PARTICIPANT_FIELDS else copy.INVALID + copy.PROMPTS[field]
        if isinstance(event, CallbackQuery):
            await event.answer(error, show_alert=True)
        else:
            await event.answer(error)
        return
    if isinstance(event, CallbackQuery):
        await event.answer()
    if data["mode"] == "settings":
        await settings_screen(event, sessions, state)
        return
    draft = dict(data["draft"], **{field: value})
    await state.update_data(draft=draft)
    position = ORDER_FIELDS.index(field)
    if data.get("editing") or position == len(ORDER_FIELDS) - 1:
        await summary(event, sessions, state)
    else:
        await prompt(event, sessions, state, ORDER_FIELDS[position + 1])


async def open_section(query: CallbackQuery, sessions, state: FSMContext):
    await query.answer()
    if query.data == "admin:settings":
        await settings_screen(query, sessions, state)
    else:
        await orders_screen(query, state, sessions)


async def navigate(query: CallbackQuery, sessions, state: FSMContext):
    action = query.data.split(":")[1:]
    if action == ["menu"]:
        await state.clear()
        await query.answer()
        if isinstance(query.message, Message):
            await query.message.edit_text(texts.ADMIN_MENU_RETURN, reply_markup=administrator_menu())
    elif action == ["new"]:
        await state.clear()
        async with sessions() as session:
            draft = await read_defaults(session)
        await state.set_data({"mode": "order", "draft": draft})
        await query.answer()
        await prompt(query, sessions, state, "name")
    elif len(action) == 2 and action[0] == "set" and action[1] in DEFAULT_FIELDS:
        await state.clear()
        async with sessions() as session:
            draft = await read_defaults(session)
        await state.set_data({"mode": "settings", "draft": draft})
        await query.answer()
        await prompt(query, sessions, state, action[1])
    else:
        await query.answer(texts.STALE_ACTION, show_alert=True)


async def form_callback(query: CallbackQuery, sessions, state: FSMContext):
    data = await state.get_data()
    parts = query.data.split(":")
    current = await state.get_state()
    if len(parts) < 3 or parts[1] != data.get("nonce") or current not in (Form.input.state, Form.summary.state, Form.edit.state):
        await query.answer(texts.STALE_ACTION, show_alert=True)
        return
    action = parts[2:]
    if action == ["cancel"]:
        await query.answer(texts.ACTION_CANCELLED)
        if data["mode"] == "settings":
            await settings_screen(query, sessions, state)
        else:
            await orders_screen(query, state, sessions)
    elif current == Form.summary.state and action == ["confirm"]:
        try:
            async with sessions.begin() as session:
                await create_order(session, query.from_user.id, data["draft"])
        except InvalidDraft:
            await query.answer(copy.INVALID_PARTICIPANT, show_alert=True)
            await summary(query, sessions, state)
            return
        # Clear before Telegram I/O so retrying delivery cannot create a second order.
        await state.clear()
        await query.answer()
        await render(query, copy.CREATED, [[(texts.ORDERS, "admin:orders")], [(texts.ADMIN_MENU, "ops:menu")]])
    elif current == Form.summary.state and action == ["edit"]:
        nonce = secrets.token_hex(6)
        await state.set_state(Form.edit)
        await state.update_data(nonce=nonce)
        await query.answer()
        await render(query, "Что изменить?", [[(copy.LABELS[field], f"form:{nonce}:field:{field}")] for field in ORDER_FIELDS]
                     + [[(texts.CANCEL, f"form:{nonce}:cancel")]])
    elif current == Form.edit.state and len(action) == 2 and action[0] == "field" and action[1] in ORDER_FIELDS:
        await state.update_data(editing=True)
        await query.answer()
        await prompt(query, sessions, state, action[1])
    elif current == Form.input.state:
        field = data["field"]
        if action == ["skip"] and field == "product_description":
            await accept_value(query, sessions, state, None)
        elif action == ["keep"] and data["draft"].get(field) is not None:
            value = data["draft"][field]
            await accept_value(query, sessions, state, str(value) if field == "waiting_minutes" else value)
        elif field in PARTICIPANT_FIELDS and len(action) == 2 and action[0] == "pick" and action[1].isascii() and action[1].isdigit():
            await accept_value(query, sessions, state, int(action[1]))
        elif field in PARTICIPANT_FIELDS and len(action) == 2 and action[0] == "page" and action[1].isascii() and action[1].isdigit() and len(action[1]) <= 6:
            await query.answer()
            await prompt(query, sessions, state, field, int(action[1]))
        else:
            await query.answer(texts.STALE_ACTION, show_alert=True)
    else:
        await query.answer(texts.STALE_ACTION, show_alert=True)


async def input_message(message: Message, sessions, state: FSMContext):
    await accept_value(message, sessions, state, message.text)


def order_creation_router():
    router = Router(name="order_creation")
    guard = RoleMiddleware(Role.ADMINISTRATOR)
    router.message.middleware(guard)
    router.callback_query.middleware(guard)
    router.callback_query.register(open_section, F.data.in_({"admin:orders", "admin:settings"}))
    router.callback_query.register(navigate, F.data.startswith("ops:"))
    router.callback_query.register(form_callback, F.data.startswith("form:"))
    router.message.register(input_message, Form.input)
    return router
