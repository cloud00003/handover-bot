"""Courier order selection, arrival, location and photo UI. No readiness action."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from app.bot import courier_texts as copy, texts
from app.bot.authorization import RoleMiddleware
from app.bot.order_creation import markup
from app.db.models import OrderStatus as S, Role, User
from app.services import courier_orders as service
from app.services.authorization import AccessDenied
from app.services.orders import StaleOrder
from app.time import format_local_datetime


class Evidence(StatesGroup):
    location = State()
    photo = State()


async def render(event, content, rows):
    keyboard = markup(rows) if rows else None
    if isinstance(event, CallbackQuery):
        if isinstance(event.message, Message):
            try:
                await event.message.edit_text(content, reply_markup=keyboard)
            except TelegramBadRequest as exc:
                if "message is not modified" not in exc.message:
                    raise
    else:
        await event.answer(content, reply_markup=keyboard)


async def error(event, content):
    if isinstance(event, CallbackQuery):
        await event.answer(content, show_alert=True)
    else:
        await event.answer(content)


async def order_text(session, order, *, include_address=True):
    customer = await session.get(User, order.customer_id)
    name = customer.display_name or customer.telegram_display_name or "Заказчик"
    content = (f"{order.name}\nДата и время: {format_local_datetime(order.scheduled_at)}\n"
               f"Заказчик: {name}\nСтатус: {texts.STATUS_LABELS[order.status.value]}")
    if include_address:
        content += f"\nАдрес: {order.pickup_address}"
    return content


async def show_orders(event, sessions, state, page=0, welcome=None):
    await state.clear()
    async with sessions() as session:
        orders = await service.list_assigned(session, event.from_user.id, page)
        summaries = [await order_text(session, order, include_address=False)
                     for order in orders[:service.PAGE_SIZE]]
    content = (welcome + "\n\n") if welcome else copy.ORDERS + "\n\n"
    content += "\n\n".join(summaries) + "\n\n" + copy.CHOOSE if orders else texts.COURIER_NO_ORDER
    rows = [[(order.name[:60], f"courier:order:{order.id}")] for order in orders[:service.PAGE_SIZE]]
    nav = []
    if page:
        nav.append((texts.PREVIOUS, f"courier:list:{page - 1}"))
    if len(orders) > service.PAGE_SIZE:
        nav.append((texts.NEXT, f"courier:list:{page + 1}"))
    if nav:
        rows.append(nav)
    await render(event, content, rows)


async def show_order(event, sessions, state, order_id):
    await state.clear()
    async with sessions() as session:
        order = await service.assigned_order(session, event.from_user.id, order_id)
        content = await order_text(session, order)
    content += "\n\nДата и время указаны по Бишкеку."
    rows = []
    if order.status == S.SCHEDULED:
        if service.utc_now() >= service.arrival_opens(order):
            rows.append([(copy.ARRIVE, f"courier:arrive:{order.id}")])
        else:
            content += "\n\n" + copy.OPENS.format(time=format_local_datetime(service.arrival_opens(order)))
            rows.append([(copy.REFRESH, f"courier:order:{order.id}")])
    elif order.status == S.COURIER_ARRIVED:
        await state.set_state(Evidence.location)
        await state.set_data({"evidence_order_id": order.id})
        content += "\n\n" + copy.LOCATION
    elif order.status == S.LOCATION_SUBMITTED:
        await state.set_state(Evidence.photo)
        await state.set_data({"evidence_order_id": order.id})
        content += "\n\n" + copy.PHOTO
    elif order.status == S.PHOTO_SUBMITTED:
        content += "\n\n" + copy.PHOTO_SAVED
        rows.append([(copy.READY, f"courier:ready:{order.id}")])
    rows.append([(copy.ORDERS, "courier:list:0")])
    await render(event, content, rows)


async def action(query: CallbackQuery, sessions, state: FSMContext):
    parts = query.data.split(":")
    if (len(parts) != 3 or parts[1] not in {"list", "order", "arrive", "ready"}
            or not parts[2].isascii() or not parts[2].isdigit() or len(parts[2]) > 18
            or (parts[1] == "list" and len(parts[2]) > 6)):
        await query.answer(texts.STALE_ACTION, show_alert=True)
        return
    action, identity = parts[1], int(parts[2])
    try:
        if action == "list":
            await query.answer()
            await show_orders(query, sessions, state, identity)
        elif action == "order":
            await show_order(query, sessions, state, identity)
            await query.answer()
        elif action == "arrive":
            async with sessions.begin() as session:
                await service.arrive(session, query.from_user.id, identity)
            await query.answer()
            await show_order(query, sessions, state, identity)
        else:
            async with sessions() as session:
                order = await service.assigned_order(session, query.from_user.id, identity)
                if order.status != S.PHOTO_SUBMITTED:
                    raise StaleOrder("Photo required")
            # Task 7 displays the prescribed button; its action belongs to Task 8.
            await query.answer(texts.SECTION_UNAVAILABLE, show_alert=True)
    except AccessDenied:
        await error(query, texts.ACCESS_DENIED)
    except service.ArrivalTooEarly:
        await error(query, copy.EARLY)
    except StaleOrder:
        await state.clear()
        await error(query, copy.STALE)


async def evidence(message: Message, sessions, state: FSMContext):
    data = await state.get_data()
    step = await state.get_state()
    order_id = data.get("evidence_order_id")
    if not order_id:
        await error(message, copy.NO_SELECTION)
        return
    try:
        async with sessions.begin() as session:
            # Revalidate identity/assignment/status even for invalid input.
            if step == Evidence.location.state:
                if message.location:
                    await service.submit_location(session, message.from_user.id, order_id,
                        latitude=message.location.latitude, longitude=message.location.longitude)
                else:
                    await service.submit_location(session, message.from_user.id, order_id, text=message.text)
            else:
                await service.submit_photo(session, message.from_user.id, order_id,
                                           message.photo[-1].file_id if message.photo else None)
        await state.clear()
        await show_order(message, sessions, state, order_id)
    except service.InvalidEvidence:
        await error(message, copy.BAD_LOCATION if step == Evidence.location.state else copy.BAD_PHOTO)
    except AccessDenied:
        await state.clear()
        await error(message, texts.ACCESS_DENIED)
    except StaleOrder:
        await state.clear()
        await error(message, copy.STALE)


async def unselected_media(message: Message):
    await message.answer(copy.NO_SELECTION)


def courier_router():
    router = Router(name="courier")
    guard = RoleMiddleware(Role.COURIER)
    router.callback_query.middleware(guard)
    router.message.middleware(guard)
    router.callback_query.register(action, F.data.startswith("courier:"))
    router.message.register(evidence, Evidence.location)
    router.message.register(evidence, Evidence.photo)
    router.message.register(unselected_media, F.photo | F.location)
    return router
