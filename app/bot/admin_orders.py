"""Protected administrator views, evidence and cancellation confirmation."""

import logging
import secrets

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import admin_order_texts as copy, texts
from app.bot.authorization import RoleMiddleware
from app.bot.order_creation import markup
from app.bot.order_texts import CREATE
from app.db.models import Role
from app.services import admin_orders as service
from app.services.orders import StaleOrder

logger = logging.getLogger(__name__)


class Cancellation(StatesGroup):
    confirm = State()


async def edit(query, text, rows):
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=markup(rows))
        except TelegramBadRequest as exc:
            if "message is not modified" not in exc.message:
                raise


def navigation(page, more, prefix):
    row = []
    if page:
        row.append((texts.PREVIOUS, f"{prefix}:{page - 1}"))
    if more:
        row.append((texts.NEXT, f"{prefix}:{page + 1}"))
    return [row] if row else []


async def show_list(query, sessions, state, history=False, page=0):
    await state.clear()
    async with sessions() as session:
        orders = await service.list_orders(session, history, page)
        summaries = [copy.brief(order, *await service.participants(session, order))
                     for order in orders[:service.PAGE_SIZE]]
    content = (texts.HISTORY if history else texts.ORDERS) + "\n\n"
    content += "\n\n".join(summaries) if summaries else (copy.EMPTY_HISTORY if history else copy.EMPTY_ACTIVE)
    content += "\n\nДата и время указаны по Бишкеку."
    rows = [] if history else [[(CREATE, "ops:new")]]
    rows += [[(order.name[:60], f"view:order:{order.id}:0")] for order in orders[:service.PAGE_SIZE]]
    rows += navigation(page, len(orders) > service.PAGE_SIZE, f"view:list:{'h' if history else 'a'}")
    rows.append([(texts.ADMIN_MENU, "ops:menu")])
    await edit(query, content, rows)


async def show_order(query, sessions, state, order_id, page=0):
    await state.clear()
    async with sessions() as session:
        order = await service.get_order(session, order_id)
        content = copy.details(order, *await service.participants(session, order),
                               await service.code_expiry(session, order_id))
    # Keep long descriptions/URLs and all milestone fields available without
    # exceeding Telegram's UTF-16 text limit (including emoji-heavy input).
    pages = [content[i:i + 1800] for i in range(0, len(content), 1800)]
    page = min(page, len(pages) - 1)
    rows = navigation(page, page + 1 < len(pages), f"view:order:{order_id}")
    if order.photo_file_id:
        rows.append([(copy.PHOTO, f"view:photo:{order_id}")])
    if order.location_type == "TELEGRAM" and order.latitude is not None and order.longitude is not None:
        rows.append([(copy.LOCATION, f"view:location:{order_id}")])
    rows.append([(copy.TIMELINE, f"view:timeline:{order_id}:0")])
    if service.can_cancel(order):
        rows.append([(copy.CANCEL, f"view:cancel:{order_id}")])
    rows.append([(texts.HISTORY if order.status in service.TERMINAL else texts.ORDERS,
                  "admin:history" if order.status in service.TERMINAL else "admin:orders")])
    rows.append([(texts.ADMIN_MENU, "ops:menu")])
    await edit(query, pages[page], rows)


async def show_timeline(query, sessions, state, order_id, page):
    await state.clear()
    async with sessions() as session:
        order = await service.get_order(session, order_id)
        events = await service.timeline(session, order_id, page)
    content = copy.TIMELINE + f" — {order.name}\n\n"
    content += "\n".join(copy.event_line(event) for event in events[:5]) or copy.EMPTY_TIMELINE
    content += "\n\nДата и время указаны по Бишкеку."
    rows = navigation(page, len(events) > 5, f"view:timeline:{order_id}")
    rows.append([("К заказу", f"view:order:{order_id}:0")])
    await edit(query, content, rows)


async def begin_cancel(query, sessions, state, order_id):
    async with sessions() as session:
        order = await service.get_order(session, order_id)
    if not service.can_cancel(order):
        await query.answer(copy.DENIED, show_alert=True)
        return
    await state.clear()
    nonce = secrets.token_hex(6)
    await state.set_state(Cancellation.confirm)
    await state.set_data({"cancel_order_id": order_id, "cancel_nonce": nonce})
    await query.answer()
    await edit(query, copy.QUESTION.format(name=order.name),
               [[(copy.CONFIRM, f"view:confirm:{nonce}")], [(copy.ABORT, f"view:order:{order_id}:0")]])


async def confirm_cancel(query, sessions, state, nonce):
    data = await state.get_data()
    if await state.get_state() != Cancellation.confirm.state or nonce != data.get("cancel_nonce"):
        await query.answer(texts.STALE_ACTION, show_alert=True)
        return
    try:
        async with sessions.begin() as session:
            order, recipients = await service.cancel_order(session, query.from_user.id, data["cancel_order_id"])
    except StaleOrder:
        await state.clear()
        await query.answer(copy.DENIED, show_alert=True)
        return
    await state.clear()
    # Deliver independently after commit. A blocked recipient cannot roll back
    # cancellation or prevent the other participant's notification attempt.
    failed = False
    for recipient in recipients:
        try:
            await query.bot.send_message(recipient, copy.NOTICE.format(name=order.name))
        except TelegramAPIError as exc:
            failed = True
            logger.warning("Cancellation notification failed (%s)", type(exc).__name__)
    await query.answer(copy.DELIVERY_FAILED if failed else copy.CANCELLED, show_alert=failed)
    await show_order(query, sessions, state, order.id)


async def section(query: CallbackQuery, sessions, state: FSMContext):
    await query.answer()
    await show_list(query, sessions, state, history=query.data == "admin:history")


async def action(query: CallbackQuery, sessions, state: FSMContext):
    parts = query.data.split(":")[1:]
    try:
        if len(parts) == 2 and parts[0] == "confirm":
            await confirm_cancel(query, sessions, state, parts[1])
            return
        if len(parts) == 3 and parts[0] == "list" and parts[1] in ("a", "h") and number(parts[2], 6):
            await query.answer()
            await show_list(query, sessions, state, parts[1] == "h", int(parts[2]))
            return
        if len(parts) not in (2, 3) or not number(parts[1], 18) or int(parts[1]) == 0:
            await query.answer(texts.STALE_ACTION, show_alert=True)
            return
        order_id = int(parts[1])
        if len(parts) == 3 and parts[0] in ("order", "timeline") and number(parts[2], 6):
            await query.answer()
            fn = show_order if parts[0] == "order" else show_timeline
            await fn(query, sessions, state, order_id, int(parts[2]))
        elif len(parts) == 2 and parts[0] == "cancel":
            await begin_cancel(query, sessions, state, order_id)
        elif len(parts) == 2 and parts[0] in ("photo", "location"):
            async with sessions() as session:
                order = await service.get_order(session, order_id)
            if isinstance(query.message, Message) and parts[0] == "photo" and order.photo_file_id:
                await query.answer()
                await query.message.answer_photo(order.photo_file_id, caption=copy.PHOTO)
            elif (isinstance(query.message, Message) and parts[0] == "location" and order.location_type == "TELEGRAM"
                  and order.latitude is not None and order.longitude is not None):
                await query.answer()
                await query.message.answer_location(order.latitude, order.longitude)
            else:
                await query.answer(copy.NO_EVIDENCE, show_alert=True)
        else:
            await query.answer(texts.STALE_ACTION, show_alert=True)
    except StaleOrder:
        await query.answer(copy.MISSING, show_alert=True)


def number(value, length):
    return value.isascii() and value.isdigit() and len(value) <= length


def admin_orders_router():
    router = Router(name="admin_orders")
    router.callback_query.middleware(RoleMiddleware(Role.ADMINISTRATOR))
    router.callback_query.register(section, F.data.in_({"admin:orders", "admin:history"}))
    router.callback_query.register(action, F.data.startswith("view:"))
    return router
