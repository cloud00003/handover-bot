import asyncio
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import Update
from sqlalchemy import func, select, update

from app.bot import courier_texts as copy, texts
from app.bot.handlers import create_dispatcher
from app.db.models import Order, OrderEvent, OrderStatus as S, PickupCode, Role, User
from app.services import courier_orders as service, orders
from app.services.admin_orders import cancel_order
from app.services.authorization import AccessDenied
from app.services.order_creation import create_order
from app.services.orders import StaleOrder
from app.time import scheduled_utc
from test_order_creation import prepared, ui, draft, screen, button
from test_persistence import database
from test_registration import callback_update, message_update


@pytest.fixture
async def courier_order(prepared, monkeypatch):
    _, factory, ids = prepared
    now = scheduled_utc(date(2026, 10, 15), time(8, 30))
    monkeypatch.setattr(service, "utc_now", lambda: now)
    monkeypatch.setattr(orders, "utc_now", lambda: now)
    async with factory.begin() as session:
        order = await create_order(session, 1, dict(draft(ids), time="09:00"))
    return order.id, now


def media(user_id=2, *, location=None, photo=False, document=False):
    value = message_update(user_id).model_dump(mode="python", by_alias=True)
    message = value["message"]
    message.pop("text", None)
    message.pop("entities", None)
    if location:
        message["location"] = dict(latitude=location[0], longitude=location[1])
    if photo:
        message["photo"] = [dict(file_id="small-photo", file_unique_id="small", width=100, height=100),
                            dict(file_id="large-photo", file_unique_id="large", width=1000, height=1000)]
    if document:
        message["document"] = dict(file_id="document", file_unique_id="document", mime_type="image/jpeg")
    return Update.model_validate(value)


async def click(ui, responses, label, user_id=2):
    return await ui(callback_update(user_id, button(responses, label)))


async def arrive_ui(ui, order_id):
    responses = await ui(callback_update(2, f"courier:order:{order_id}"))
    return await click(ui, responses, copy.ARRIVE)


@pytest.mark.parametrize("offset,allowed", [(-1, False), (0, True), (1, True), (3600, True), (172800, True)])
async def test_arrival_window_and_no_late_cutoff(prepared, courier_order, monkeypatch, offset, allowed):
    _, factory, _ = prepared
    order_id, opening = courier_order
    monkeypatch.setattr(service, "utc_now", lambda: opening + timedelta(seconds=offset))
    async with factory.begin() as session:
        if allowed:
            await service.arrive(session, 2, order_id)
        else:
            with pytest.raises(service.ArrivalTooEarly):
                await service.arrive(session, 2, order_id)
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == (S.COURIER_ARRIVED if allowed else S.SCHEDULED)


def test_midnight_opening_never_precedes_scheduled_bishkek_date():
    order = Order(scheduled_at=scheduled_utc(date(2026, 10, 15), time(0, 10)))
    expected = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)
    assert service.arrival_opens(order) == expected


async def test_midnight_boundary_ui_and_service(prepared, courier_order, ui, monkeypatch):
    _, factory, _ = prepared
    order_id, _ = courier_order
    scheduled = scheduled_utc(date(2026, 10, 15), time(0, 10))
    async with factory.begin() as session:
        await session.execute(update(Order).where(Order.id == order_id).values(scheduled_at=scheduled))
    opening = scheduled - timedelta(minutes=10)
    monkeypatch.setattr(service, "utc_now", lambda: opening - timedelta(microseconds=1))
    responses = await ui(callback_update(2, f"courier:order:{order_id}"))
    assert "15.10.2026 00:00" in screen(responses).text
    assert copy.ARRIVE not in [b.text for row in screen(responses).reply_markup.inline_keyboard for b in row]
    assert (await ui(callback_update(2, f"courier:arrive:{order_id}")))[0].text == copy.EARLY
    monkeypatch.setattr(service, "utc_now", lambda: opening)
    assert copy.LOCATION in screen(await ui(callback_update(2, f"courier:arrive:{order_id}"))).text


async def test_start_lists_all_assigned_active_orders_and_requires_choice(ui, prepared, courier_order):
    _, factory, ids = prepared
    order_id, _ = courier_order
    async with factory.begin() as session:
        for i in range(4):
            await create_order(session, 1, dict(draft(ids), name=f"Передача {i}"))
        await create_order(session, 1, dict(draft(ids), name="Другой курьер", courier_id=ids[3]))
        terminal = await create_order(session, 1, dict(draft(ids), name="Завершённый"))
        terminal._status = S.COMPLETED
    responses = await ui(message_update(2))
    assert screen(responses).text.startswith(texts.COURIER_GUIDANCE)
    assert "Другой курьер" not in screen(responses).text
    assert "Завершённый" not in screen(responses).text
    assert copy.ARRIVE not in [b.text for row in screen(responses).reply_markup.inline_keyboard for b in row]
    responses = await click(ui, responses, texts.NEXT)
    responses = await click(ui, responses, "Ноутбук")
    for value in ("Ноутбук", "15.10.2026 09:00", "Бишкек", "Айдана", "Запланировано"):
        assert value in screen(responses).text
    assert button(responses, copy.ARRIVE) == f"courier:arrive:{order_id}"


@pytest.mark.parametrize("location_type", ["TELEGRAM", "2GIS"])
async def test_full_arrival_location_photo_flow_persistence_and_no_task8(ui, prepared, courier_order, location_type):
    engine, factory, _ = prepared
    order_id, now = courier_order
    responses = await arrive_ui(ui, order_id)
    assert copy.LOCATION in screen(responses).text
    raw_url = "https://2gis.kg/bishkek/geo/123?m=74.6%2C42.8%2F16&x=1#point"
    responses = (await ui(media(location=(42.87, 74.6))) if location_type == "TELEGRAM"
                 else await ui(message_update(2, "Точка выдачи: " + raw_url)))
    assert copy.PHOTO in screen(responses).text
    responses = await ui(media(photo=True))
    assert copy.PHOTO_SAVED in screen(responses).text
    ready = button(responses, copy.READY)
    assert (await ui(callback_update(2, ready)))[0].text == texts.SECTION_UNAVAILABLE
    await engine.dispose()
    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order.status == S.PHOTO_SUBMITTED and order.photo_file_id == "large-photo"
        assert order.location_type == location_type
        assert order.location_url == (raw_url if location_type == "2GIS" else None)
        assert order.latitude == (42.87 if location_type == "TELEGRAM" else None)
        assert order.longitude == (74.6 if location_type == "TELEGRAM" else None)
        assert order.arrived_at == order.location_submitted_at == order.photo_submitted_at == now
        events = list((await session.scalars(select(OrderEvent).where(OrderEvent.order_id == order_id)
                                            .order_by(OrderEvent.id))).all())
        assert [event.event_type for event in events] == ["ORDER_CREATED", "COURIER_ARRIVED", "LOCATION_SUBMITTED", "PHOTO_SUBMITTED"]
        assert all(event.actor_telegram_user_id == 2 and event.actor_role == Role.COURIER for event in events[1:])
        assert all(event.occurred_at.utcoffset() == timedelta(0) for event in events)
        assert events[2].details["location_type"] == location_type
        assert events[3].details["photo_file_id"] == "large-photo"
        assert order.ready_at is None
        assert await session.scalar(select(func.count()).select_from(PickupCode)) == 0


@pytest.mark.parametrize("raw", ["https://2gis.kg/bishkek/geo/123?x=1%2F2#map", "https://go.2gis.com/abc", "https://2gis.ru/moscow/geo/12",
                                "https://2gis.kz/almaty/geo/123", "https://2gis.com/dubai/geo/123!"])
def test_valid_2gis_url_preserved(raw):
    assert service.location_link("Место: " + raw)["location_url"] == raw


@pytest.mark.parametrize("value", [None, "нет ссылки", "https://example.com/2gis.kg/point", "https://2gis.kg.evil.com/point",
    "https://2gis.kg@evil.com/point", "https://evil@2gis.kg/point", "https://2gis.kg:444/point", "https://2gis.kg/",
    "ftp://2gis.kg/point", "https://2gis.kg/" + "x" * 2001])
def test_invalid_2gis_links_rejected(value):
    with pytest.raises(service.InvalidEvidence):
        service.location_link(value)


@pytest.mark.parametrize("latitude,longitude", [(91, 0), (0, 181), (float("nan"), 0), (0, float("inf")), (None, None), (True, 0)])
def test_invalid_coordinates(latitude, longitude):
    with pytest.raises(service.InvalidEvidence):
        service.coordinates(latitude, longitude)


async def test_required_sequence_invalid_input_and_duplicate_media(ui, prepared, courier_order):
    _, factory, _ = prepared
    order_id, _ = courier_order
    assert (await ui(media(photo=True)))[0].text == copy.NO_SELECTION
    async with factory.begin() as session:
        with pytest.raises(StaleOrder):
            await service.submit_photo(session, 2, order_id, "early-photo")
        with pytest.raises(StaleOrder):
            await service.submit_location(session, 2, order_id, latitude=42, longitude=74)
    await arrive_ui(ui, order_id)
    assert (await ui(message_update(2, "не ссылка")))[0].text == copy.BAD_LOCATION
    assert (await ui(media(photo=True)))[0].text == copy.BAD_LOCATION
    await ui(media(location=(42, 74)))
    assert (await ui(media(document=True)))[0].text == copy.BAD_PHOTO
    assert (await ui(media(location=(42, 74))))[0].text == copy.BAD_PHOTO
    await ui(media(photo=True))
    assert (await ui(media(photo=True)))[0].text == copy.NO_SELECTION
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 4


@pytest.mark.parametrize("identity", [1, 3, 4, 99])
@pytest.mark.parametrize("action", ["order", "arrive", "ready"])
async def test_only_assigned_courier_can_act(ui, courier_order, identity, action):
    order_id, _ = courier_order
    responses = await ui(callback_update(identity, f"courier:{action}:{order_id}"))
    assert responses[0].text == texts.ACCESS_DENIED


async def test_deactivation_and_reassignment_between_prompt_and_submission(ui, prepared, courier_order):
    _, factory, ids = prepared
    order_id, _ = courier_order
    await arrive_ui(ui, order_id)
    async with factory.begin() as session:
        await session.execute(update(Order).where(Order.id == order_id).values(courier_id=ids[3]))
    assert (await ui(media(location=(42, 74))))[0].text == texts.ACCESS_DENIED
    async with factory.begin() as session:
        await session.execute(update(User).where(User.id == ids[3]).values(active=False))
        with pytest.raises(AccessDenied):
            await service.submit_location(session, 4, order_id, latitude=42, longitude=74)
    async with factory() as session:
        assert (await session.get(Order, order_id)).location_submitted_at is None


@pytest.mark.parametrize("status", [S.COMPLETED, S.CANCELLED, S.CUSTOMER_NO_SHOW, S.DISPUTED])
async def test_terminal_orders_reject_all_writes(prepared, courier_order, status):
    _, factory, _ = prepared
    order_id, _ = courier_order
    async with factory.begin() as session:
        await session.execute(update(Order).where(Order.id == order_id).values(_status=status))
    async with factory.begin() as session:
        for fn, args in ((service.arrive, {}), (service.submit_location, dict(latitude=42, longitude=74)),
                         (service.submit_photo, dict(file_id="photo"))):
            with pytest.raises(StaleOrder):
                await fn(session, 2, order_id, **args)


async def test_cancel_after_prompt_rejects_location(ui, prepared, courier_order):
    _, factory, _ = prepared
    order_id, _ = courier_order
    await arrive_ui(ui, order_id)
    async with factory.begin() as session:
        await cancel_order(session, 1, order_id)
    assert (await ui(media(location=(42, 74))))[0].text == copy.STALE
    async with factory() as session:
        assert (await session.get(Order, order_id)).location_submitted_at is None


@pytest.mark.parametrize("step", ["arrival", "location", "photo"])
async def test_concurrent_duplicate_actions_record_once(prepared, courier_order, step):
    _, factory, _ = prepared
    order_id, _ = courier_order
    async with factory.begin() as session:
        if step != "arrival":
            await service.arrive(session, 2, order_id)
        if step == "photo":
            await service.submit_location(session, 2, order_id, latitude=42, longitude=74)
    async def submit():
        async with factory.begin() as session:
            if step == "arrival":
                return await service.arrive(session, 2, order_id)
            if step == "location":
                return await service.submit_location(session, 2, order_id, latitude=42, longitude=74)
            return await service.submit_photo(session, 2, order_id, "photo")
    results = await asyncio.gather(submit(), submit(), return_exceptions=True)
    assert sum(isinstance(value, Order) for value in results) == 1
    assert sum(isinstance(value, StaleOrder) for value in results) == 1


@pytest.mark.parametrize("step", ["arrival", "location", "photo"])
async def test_audit_failure_rolls_back_evidence_and_status(prepared, courier_order, monkeypatch, step):
    _, factory, _ = prepared
    order_id, _ = courier_order
    async with factory.begin() as session:
        if step != "arrival":
            await service.arrive(session, 2, order_id)
        if step == "photo":
            await service.submit_location(session, 2, order_id, latitude=42, longitude=74)
    async def fail(*args, **kwargs):
        raise RuntimeError("audit failure")
    monkeypatch.setattr(orders, "append_event", fail)
    async with factory.begin() as session:
        with pytest.raises(RuntimeError):
            if step == "arrival":
                await service.arrive(session, 2, order_id)
            elif step == "location":
                await service.submit_location(session, 2, order_id, latitude=42, longitude=74)
            else:
                await service.submit_photo(session, 2, order_id, "photo")
    async with factory() as session:
        order = await session.get(Order, order_id)
        if step == "arrival":
            assert order.status == S.SCHEDULED and order.arrived_at is None
        elif step == "location":
            assert order.status == S.COURIER_ARRIVED and order.latitude is None and order.location_submitted_at is None
        else:
            assert order.status == S.LOCATION_SUBMITTED and order.photo_file_id is None and order.photo_submitted_at is None


async def test_reopening_order_restores_prompt_after_dispatcher_restart(prepared, courier_order, token):
    _, factory, _ = prepared
    order_id, _ = courier_order
    async with factory.begin() as session:
        await service.arrive(session, 2, order_id)
    async with Bot(token).context() as bot:
        for expected, submission in ((copy.LOCATION, media(location=(42, 74))), (copy.PHOTO, media(photo=True))):
            dispatcher = create_dispatcher(sessions=factory)
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback_update(2, f"courier:order:{order_id}"))
            responses = [c.args[1] for c in bot.session.make_request.call_args_list]
            assert expected in screen(responses).text
            await dispatcher.feed_update(bot, submission)
            await dispatcher.fsm.close()
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == S.PHOTO_SUBMITTED


async def test_duplicate_arrival_callback_never_overwrites_timestamp(ui, prepared, courier_order):
    _, factory, _ = prepared
    order_id, now = courier_order
    await arrive_ui(ui, order_id)
    assert (await ui(callback_update(2, f"courier:arrive:{order_id}")))[0].text == copy.STALE
    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order.arrived_at == now
        assert await session.scalar(select(func.count()).select_from(OrderEvent).where(
            OrderEvent.event_type == "COURIER_ARRIVED")) == 1


async def test_cancellation_racing_location_keeps_terminal_state(prepared, courier_order):
    _, factory, _ = prepared
    order_id, _ = courier_order
    async with factory.begin() as session:
        await service.arrive(session, 2, order_id)
    async def cancel():
        async with factory.begin() as session:
            return await cancel_order(session, 1, order_id)
    async def location():
        async with factory.begin() as session:
            return await service.submit_location(session, 2, order_id, latitude=42, longitude=74)
    results = await asyncio.gather(cancel(), location(), return_exceptions=True)
    assert isinstance(results[0], tuple)
    assert isinstance(results[1], (Order, StaleOrder))
    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order.status == S.CANCELLED
        events = list((await session.scalars(select(OrderEvent).where(OrderEvent.order_id == order_id)
                                            .order_by(OrderEvent.id))).all())
        assert events[-1].event_type == "CANCELLED"
        assert (order.location_submitted_at is not None) == any(e.event_type == "LOCATION_SUBMITTED" for e in events)


async def test_courier_evidence_visible_in_administrator_views(ui, prepared, courier_order):
    order_id, _ = courier_order
    await arrive_ui(ui, order_id)
    await ui(message_update(2, "https://2gis.kg/bishkek/geo/123"))
    await ui(media(photo=True))
    responses = await ui(callback_update(1, f"view:order:{order_id}:0"))
    assert "https://2gis.kg/bishkek/geo/123" in screen(responses).text
    assert "Фото товара: Добавлено" in screen(responses).text
    responses = await ui(callback_update(1, f"view:timeline:{order_id}:0"))
    for label in ("Курьер на месте", "Местоположение отправлено", "Фото товара получено"):
        assert label in screen(responses).text
