import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendLocation, SendMessage, SendPhoto
from sqlalchemy import func, select, update

from app.bot import admin_order_texts as copy, texts
from app.bot.handlers import create_dispatcher
from app.db.base import utc_now
from app.db.models import Order, OrderEvent, OrderStatus as S, PickupCode, Role, User
from app.db.repositories import replace_code_record, consume_code_record
from app.services.admin_orders import cancel_order, TERMINAL
from app.services.authorization import AccessDenied
from app.services.order_creation import create_order
from app.services.orders import StaleOrder, transition_order
from app.services.state_machine import TRANSITIONS, InvalidTransition
from test_order_creation import prepared, ui, draft, screen, button, click
from test_persistence import database
from test_registration import callback_update


@pytest.fixture
async def order_id(prepared):
    _, factory, ids = prepared
    async with factory.begin() as session:
        order = await create_order(session, 1, draft(ids))
    return order.id


async def open_order(ui, order_id):
    return await ui(callback_update(1, f"view:order:{order_id}:0"))


async def cancellation(ui, order_id):
    responses = await open_order(ui, order_id)
    return await click(ui, responses, copy.CANCEL)


async def all_detail_text(ui, responses):
    content = screen(responses).text
    while any(b.text == texts.NEXT for row in screen(responses).reply_markup.inline_keyboard for b in row):
        responses = await click(ui, responses, texts.NEXT)
        content += screen(responses).text
    return content


@pytest.mark.parametrize("status", list(S))
async def test_active_and_historical_lists_and_cancellation_availability(ui, prepared, order_id, status):
    _, factory, _ = prepared
    async with factory.begin() as session:
        await session.execute(update(Order).where(Order.id == order_id).values(_status=status))
    active = screen(await ui(callback_update(1, "admin:orders")))
    history = screen(await ui(callback_update(1, "admin:history")))
    selected, other = (history, active) if status in TERMINAL else (active, history)
    for value in ("Ноутбук", "Алексей", "Айдана", "15.10.2026 00:30", texts.STATUS_LABELS[status.value]):
        assert value in selected.text
    assert "Ноутбук" not in other.text
    responses = await open_order(ui, order_id)
    labels = [b.text for row in screen(responses).reply_markup.inline_keyboard for b in row]
    assert (copy.CANCEL in labels) == (S.CANCELLED in TRANSITIONS[status])
    timeline = await click(ui, responses, copy.TIMELINE)
    assert "Заказ создан" in screen(timeline).text


@pytest.mark.parametrize("location", ["TELEGRAM", "2GIS"])
async def test_details_evidence_milestones_and_secrets(ui, prepared, order_id, location):
    _, factory, _ = prepared
    now = utc_now()
    async with factory.begin() as session:
        await session.execute(update(Order).where(Order.id == order_id).values(
            product_description="Комплект товара", photo_file_id="test-photo-file", location_type=location,
            latitude=42.87, longitude=74.6, location_url="https://2gis.kg/bishkek/geo/123",
            **{field: now for field in copy.TIMES},
        ))
        await replace_code_record(session, order_id=order_id, code_hash="SECRET-HASH-SENTINEL")
        session.add(OrderEvent(order_id=order_id, event_type="READY_FOR_PICKUP", actor_role=Role.COURIER,
                               details={"code": "SECRET-CODE-SENTINEL", "hash": "SECRET-HASH-SENTINEL"}))
    responses = await open_order(ui, order_id)
    content = await all_detail_text(ui, responses)
    for label in copy.TIMES.values():
        assert label + ":" in content
    assert "Последний код действителен до:" in content
    assert "Комплект товара" in content
    assert ("42.87, 74.6" if location == "TELEGRAM" else "https://2gis.kg/bishkek/geo/123") in content
    assert "SECRET" not in content
    photo = await click(ui, responses, copy.PHOTO)
    assert isinstance(photo[-1], SendPhoto) and photo[-1].photo == "test-photo-file"
    if location == "TELEGRAM":
        result = await click(ui, responses, copy.LOCATION)
        assert isinstance(result[-1], SendLocation)
        assert result[-1].latitude == 42.87 and result[-1].longitude == 74.6
    timeline = await click(ui, responses, copy.TIMELINE)
    assert texts.STATUS_LABELS["READY_FOR_PICKUP"] in screen(timeline).text
    assert "SECRET" not in screen(timeline).text


async def test_empty_views_missing_evidence_and_unknown_callbacks(ui, order_id):
    assert copy.EMPTY_HISTORY in screen(await ui(callback_update(1, "admin:history"))).text
    content = await all_detail_text(ui, await open_order(ui, order_id))
    assert copy.NOT_SET in content
    assert (await ui(callback_update(1, f"view:photo:{order_id}")))[0].text == copy.NO_EVIDENCE
    assert (await ui(callback_update(1, "view:order:999999:0")))[-1].text == copy.MISSING
    for value in ("view:", "view:cancel:-1", "view:list:a:bad", "view:timeline:1:-1"):
        assert (await ui(callback_update(1, value)))[0].text == texts.STALE_ACTION


async def test_cancel_abort_then_confirm_records_once_and_notifies(ui, prepared, order_id):
    _, factory, _ = prepared
    async with factory.begin() as session:
        code = await replace_code_record(session, order_id=order_id, code_hash="private-hash")
    responses = await cancellation(ui, order_id)
    old_confirm = button(responses, copy.CONFIRM)
    await click(ui, responses, copy.ABORT)
    assert (await ui(callback_update(1, old_confirm)))[0].text == texts.STALE_ACTION
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == S.SCHEDULED
        assert (await session.get(PickupCode, code.id)).invalidated_at is None
    responses = await cancellation(ui, order_id)
    confirm = button(responses, copy.CONFIRM)
    responses = await ui(callback_update(1, confirm))
    notifications = [response for response in responses if isinstance(response, SendMessage)]
    assert {response.chat_id for response in notifications} == {2, 3}
    assert all("отменён администратором" in response.text for response in notifications)
    assert "Отменено" in await all_detail_text(ui, responses)
    assert (await ui(callback_update(1, confirm)))[0].text == texts.STALE_ACTION
    async with factory.begin() as session:
        order = await session.get(Order, order_id)
        event = await session.scalar(select(OrderEvent).where(OrderEvent.event_type == "CANCELLED"))
        assert order.status == S.CANCELLED
        assert order.cancelled_at == event.occurred_at
        assert event.actor_telegram_user_id == 1 and event.actor_role == Role.ADMINISTRATOR
        assert event.details["previous_status"] == "SCHEDULED"
        assert (await session.get(PickupCode, code.id)).invalidated_at == order.cancelled_at
        assert not await consume_code_record(session, code_id=code.id, order_id=order_id)
        assert await session.scalar(select(func.count()).select_from(OrderEvent).where(OrderEvent.event_type == "CANCELLED")) == 1
        for target in S:
            with pytest.raises(InvalidTransition):
                await transition_order(session, order_id=order_id, expected_status=S.CANCELLED,
                                       target_status=target, actor_telegram_user_id=2, actor_role=Role.COURIER)


@pytest.mark.parametrize("status", list(S))
async def test_service_cancellation_respects_every_state(prepared, order_id, status):
    _, factory, _ = prepared
    async with factory.begin() as session:
        await session.execute(update(Order).where(Order.id == order_id).values(_status=status))
    async with factory.begin() as session:
        if S.CANCELLED in TRANSITIONS[status]:
            order, recipients = await cancel_order(session, 1, order_id)
            assert order.status == S.CANCELLED and recipients == (2, 3)
        else:
            with pytest.raises(StaleOrder):
                await cancel_order(session, 1, order_id)


async def test_successful_verification_after_prompt_rejects_stale_confirmation(ui, prepared, order_id):
    _, factory, _ = prepared
    responses = await cancellation(ui, order_id)
    async with factory.begin() as session:
        await session.execute(update(Order).where(Order.id == order_id).values(
            _status=S.AWAITING_CUSTOMER_CONFIRMATION, code_verified_at=utc_now()))
    responses = await click(ui, responses, copy.CONFIRM)
    assert responses[0].text == copy.DENIED
    assert not any(isinstance(r, SendMessage) for r in responses)
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == S.AWAITING_CUSTOMER_CONFIRMATION


async def test_concurrent_service_cancellation_one_event(prepared, order_id):
    _, factory, _ = prepared
    async def cancel():
        async with factory.begin() as session:
            return await cancel_order(session, 1, order_id)
    results = await asyncio.gather(cancel(), cancel(), return_exceptions=True)
    assert sum(isinstance(value, tuple) for value in results) == 1
    assert sum(isinstance(value, StaleOrder) for value in results) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(OrderEvent).where(OrderEvent.event_type == "CANCELLED")) == 1


@pytest.mark.parametrize("identity", [2, 3, 99])
@pytest.mark.parametrize("action", ["admin:orders", "admin:history", "view:order:1:0", "view:timeline:1:0",
    "view:photo:1", "view:location:1", "view:cancel:1", "view:confirm:fake"])
async def test_all_view_actions_authorized_by_current_role(ui, identity, action):
    responses = await ui(callback_update(identity, action))
    assert len(responses) == 1 and responses[0].text == texts.ACCESS_DENIED


async def test_inactive_admin_and_wrong_role_service_rejected(ui, prepared, order_id):
    _, factory, ids = prepared
    responses = await cancellation(ui, order_id)
    async with factory.begin() as session:
        with pytest.raises(AccessDenied):
            await cancel_order(session, 2, order_id)
        await session.execute(update(User).where(User.id == ids[0]).values(active=False))
    assert (await click(ui, responses, copy.CONFIRM))[0].text == texts.ACCESS_DENIED


async def test_audit_failure_rolls_back_cancellation_and_code(prepared, order_id, monkeypatch):
    _, factory, _ = prepared
    async with factory.begin() as session:
        code = await replace_code_record(session, order_id=order_id, code_hash="secret")
    from app.services import orders
    async def fail(*args, **kwargs):
        raise RuntimeError("audit failure")
    monkeypatch.setattr(orders, "append_event", fail)
    async with factory.begin() as session:
        with pytest.raises(RuntimeError):
            await cancel_order(session, 1, order_id)
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == S.SCHEDULED
        assert (await session.get(PickupCode, code.id)).invalidated_at is None


async def test_notification_failure_preserves_cancellation_and_attempts_other_recipient(prepared, order_id, token, caplog):
    _, factory, _ = prepared
    dispatcher = create_dispatcher(sessions=factory)
    async with Bot(token).context() as bot:
        async def transport(bot, method, **kwargs):
            if isinstance(method, SendMessage) and method.chat_id == 2:
                raise TelegramForbiddenError(method=method, message="PRIVATE-SENTINEL")
            return True
        bot.session.make_request = AsyncMock(side_effect=transport)
        await dispatcher.feed_update(bot, callback_update(1, f"view:cancel:{order_id}"))
        responses = [c.args[1] for c in bot.session.make_request.call_args_list]
        value = button(responses, copy.CONFIRM)
        bot.session.make_request.reset_mock()
        await dispatcher.feed_update(bot, callback_update(1, value))
        responses = [c.args[1] for c in bot.session.make_request.call_args_list]
        assert {r.chat_id for r in responses if isinstance(r, SendMessage)} == {2, 3}
        assert any(getattr(r, "text", None) == copy.DELIVERY_FAILED for r in responses)
        assert "PRIVATE-SENTINEL" not in caplog.text
    await dispatcher.fsm.close()
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == S.CANCELLED


async def test_list_details_timeline_pagination_and_historical_names(ui, prepared, order_id):
    _, factory, ids = prepared
    async with factory.begin() as session:
        for i in range(4):
            await create_order(session, 1, dict(draft(ids), name=f"Дополнительный {i}"))
        await session.execute(update(Order).where(Order.id == order_id).values(product_description="📦" * 4000))
        await session.execute(update(User).where(User.id == ids[1]).values(active=False))
        session.add_all([OrderEvent(order_id=order_id, event_type="COURIER_ARRIVED", actor_role=Role.COURIER)
                         for _ in range(7)])
    responses = await ui(callback_update(1, "admin:orders"))
    assert "Алексей" in screen(responses).text
    responses = await click(ui, responses, texts.NEXT)
    assert "Дополнительный 3" in screen(responses).text
    responses = await open_order(ui, order_id)
    content = ""
    while True:
        page = screen(responses)
        assert len(page.text.encode("utf-16-le")) // 2 <= 4096
        content += page.text
        if not any(b.text == texts.NEXT for row in page.reply_markup.inline_keyboard for b in row):
            break
        responses = await click(ui, responses, texts.NEXT)
    assert "📦" * 4000 in content
    responses = await click(ui, responses, copy.TIMELINE)
    assert "Заказ создан" in screen(responses).text
    responses = await click(ui, responses, texts.NEXT)
    assert "Курьер на месте" in screen(responses).text
    responses = await click(ui, responses, "К заказу")
    assert "Алексей" in screen(responses).text


async def test_verified_timestamp_alone_forbids_cancellation(prepared, order_id):
    _, factory, _ = prepared
    async with factory.begin() as session:
        await session.execute(update(Order).where(Order.id == order_id).values(code_verified_at=utc_now()))
    async with factory.begin() as session:
        with pytest.raises(StaleOrder):
            await cancel_order(session, 1, order_id)


async def test_concurrent_confirmation_records_once(ui, prepared, order_id):
    _, factory, _ = prepared
    responses = await cancellation(ui, order_id)
    value = button(responses, copy.CONFIRM)
    await asyncio.gather(ui(callback_update(1, value)), ui(callback_update(1, value)))
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(OrderEvent).where(OrderEvent.event_type == "CANCELLED")) == 1
