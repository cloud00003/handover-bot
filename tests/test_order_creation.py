import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import func, select, update

from app.bot import order_texts as copy, texts
from app.bot.handlers import create_dispatcher
from app.db.models import Defaults, Order, OrderEvent, OrderStatus, Role, User
from app.services.authorization import AccessDenied
from app.services.order_creation import InvalidDraft, create_order, read_defaults, save_default, validate_field
from test_persistence import database
from test_registration import callback_update, message_update


@pytest.fixture
async def prepared(database):
    engine, factory, _ = database
    async with factory.begin() as session:
        users = [User(telegram_user_id=1, role=Role.ADMINISTRATOR),
                 User(telegram_user_id=2, role=Role.COURIER, display_name="Алексей"),
                 User(telegram_user_id=3, role=Role.CUSTOMER, display_name="Айдана"),
                 User(telegram_user_id=4, role=Role.COURIER, display_name="Борис"),
                 User(telegram_user_id=5, role=Role.CUSTOMER, display_name="Дина")]
        session.add_all(users)
        await session.flush()
        defaults = await session.get(Defaults, 1)
        defaults.courier_id, defaults.customer_id = users[1].id, users[2].id
        defaults.pickup_address = "Бишкек, адрес выдачи"
    return engine, factory, [user.id for user in users]


@pytest.fixture
async def ui(prepared, token):
    _, factory, _ = prepared
    dispatcher = create_dispatcher(sessions=factory)
    async with Bot(token).context() as bot:
        bot.session.make_request = AsyncMock(return_value=True)

        async def send(event):
            bot.session.make_request.reset_mock()
            await dispatcher.feed_update(bot, event)
            return [call.args[1] for call in bot.session.make_request.call_args_list]

        yield send
    await dispatcher.fsm.close()


def screen(responses):
    return next(item for item in reversed(responses) if isinstance(item, (SendMessage, EditMessageText)))


def button(responses, label):
    return next(b.callback_data for row in screen(responses).reply_markup.inline_keyboard
                for b in row if b.text == label)


async def click(ui, responses, label):
    return await ui(callback_update(1, button(responses, label)))


async def draft_summary(ui, *, override=False, description=None):
    responses = await ui(callback_update(1, "admin:orders"))
    responses = await click(ui, responses, copy.CREATE)
    for value in ("Ноутбук", "15.10.2026", "14:30"):
        responses = await ui(message_update(1, value))
    responses = (await ui(message_update(1, "Другой адрес")) if override
                 else await click(ui, responses, copy.KEEP))
    responses = await click(ui, responses, "Борис" if override else copy.KEEP)
    responses = await click(ui, responses, "Дина" if override else copy.KEEP)
    return (await ui(message_update(1, description)) if description
            else await click(ui, responses, copy.SKIP))


def draft(ids):
    return dict(name="Ноутбук", date="15.10.2026", time="00:30", pickup_address="Бишкек",
                courier_id=ids[1], customer_id=ids[2], product_description=None)


@pytest.mark.parametrize("override,description", [(False, None), (True, "Ноутбук и зарядка")])
async def test_complete_creation_defaults_overrides_and_atomic_event(ui, prepared, override, description):
    engine, factory, ids = prepared
    responses = await draft_summary(ui, override=override, description=description)
    summary = screen(responses)
    for value in ("Ноутбук", "15.10.2026", "14:30", "Другой адрес" if override else "Бишкек, адрес выдачи",
                  "Борис" if override else "Алексей", "Дина" if override else "Айдана",
                  description or "Не указано"):
        assert value in summary.text
    assert [row[0].text for row in summary.reply_markup.inline_keyboard] == [copy.CREATE, copy.EDIT, texts.CANCEL]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 0
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 0
    confirmation = button(responses, copy.CREATE)
    responses = await ui(callback_update(1, confirmation))
    assert screen(responses).text == copy.CREATED
    responses = await ui(callback_update(1, confirmation))
    assert responses[0].text == texts.STALE_ACTION
    await engine.dispose()
    async with factory() as session:
        order = await session.scalar(select(Order))
        assert order.status == OrderStatus.SCHEDULED
        assert order.courier_id == ids[3 if override else 1]
        assert order.customer_id == ids[4 if override else 2]
        assert order.product_description == description
        assert order.waiting_minutes == 15
        assert order.scheduled_at.utcoffset() == timedelta(0)
        assert order.scheduled_at.hour == 8
        event = await session.scalar(select(OrderEvent))
        assert event.order_id == order.id and event.event_type == "ORDER_CREATED"
        assert event.actor_telegram_user_id == 1 and event.actor_role == Role.ADMINISTRATOR
        assert event.occurred_at == order.created_at
        assert await session.scalar(select(func.count()).select_from(Order)) == 1
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 1


async def test_edit_and_cancel_never_persist_draft(ui, prepared):
    _, factory, _ = prepared
    responses = await draft_summary(ui)
    stale_confirm = button(responses, copy.CREATE)
    responses = await click(ui, responses, copy.EDIT)
    responses = await click(ui, responses, copy.LABELS["name"])
    responses = await ui(message_update(1, "Новое название"))
    assert "Новое название" in screen(responses).text
    assert "15.10.2026" in screen(responses).text
    assert (await ui(callback_update(1, stale_confirm)))[0].text == texts.STALE_ACTION
    await click(ui, responses, texts.CANCEL)
    assert (await ui(callback_update(1, button(responses, copy.CREATE))))[0].text == texts.STALE_ACTION
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 0


async def test_concurrent_confirmation_creates_one_order(ui, prepared):
    _, factory, _ = prepared
    responses = await draft_summary(ui)
    value = button(responses, copy.CREATE)
    await asyncio.gather(ui(callback_update(1, value)), ui(callback_update(1, value)))
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 1
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 1


async def test_settings_persist_and_existing_orders_keep_waiting_snapshot(ui, prepared):
    engine, factory, ids = prepared
    async with factory.begin() as session:
        existing = await create_order(session, 1, draft(ids))
    for field, value in (("pickup_address", "Новая точка"), ("waiting_minutes", "25")):
        responses = await ui(callback_update(1, "admin:settings"))
        responses = await click(ui, responses, copy.LABELS[field])
        responses = await ui(message_update(1, value))
        assert value in screen(responses).text
    for field, name in (("courier_id", "Борис"), ("customer_id", "Дина")):
        responses = await ui(callback_update(1, "admin:settings"))
        responses = await click(ui, responses, copy.LABELS[field])
        responses = await click(ui, responses, name)
        assert name in screen(responses).text
    await engine.dispose()
    async with factory.begin() as session:
        settings = await read_defaults(session)
        assert settings == dict(pickup_address="Новая точка", waiting_minutes=25,
                                courier_id=ids[3], customer_id=ids[4])
        assert (await session.get(Order, existing.id)).waiting_minutes == 15
        assert (await create_order(session, 1, draft(ids))).waiting_minutes == 25
        assert (await session.get(Defaults, 1)).code_lifetime_minutes == 20


@pytest.mark.parametrize("field,value", [("name", " "), ("name", "x" * 201), ("name", "a\nb"),
    ("date", "31.02.2026"), ("date", "2026-10-15"), ("time", "24:00"), ("time", "9:30"),
    ("pickup_address", ""), ("pickup_address", "x" * 501), ("product_description", "x" * 1001),
    ("waiting_minutes", "0"), ("waiting_minutes", "-1"), ("waiting_minutes", "1.5"),
    ("waiting_minutes", "2147483648"), ("courier_id", None), ("customer_id", "1")])
def test_invalid_fields(field, value):
    with pytest.raises(InvalidDraft):
        validate_field(field, value)


async def test_ui_invalid_input_and_stale_buttons(ui, prepared):
    responses = await ui(callback_update(1, "ops:new"))
    assert (await ui(message_update(1, " ")))[-1].text.startswith(copy.INVALID)
    await ui(message_update(1, "Название"))
    assert (await ui(message_update(1, "31.02.2026")))[-1].text.startswith(copy.INVALID)
    await ui(message_update(1, "15.10.2026"))
    assert (await ui(message_update(1, "24:00")))[-1].text.startswith(copy.INVALID)
    responses = await ui(message_update(1, "12:00"))
    old_keep = button(responses, copy.KEEP)
    await click(ui, responses, copy.KEEP)
    assert (await ui(callback_update(1, old_keep)))[0].text == texts.STALE_ACTION
    await ui(message_update(1))  # /start abandons the draft
    assert (await ui(callback_update(1, old_keep)))[0].text == texts.STALE_ACTION


async def test_deactivated_default_and_missing_defaults_are_not_used(ui, prepared):
    _, factory, ids = prepared
    async with factory.begin() as session:
        await session.execute(update(User).where(User.id == ids[1]).values(active=False))
        settings = await session.get(Defaults, 1)
        settings.customer_id = None
        settings.pickup_address = None
    responses = await ui(callback_update(1, "ops:new"))
    for value in ("Название", "15.10.2026", "12:00"):
        responses = await ui(message_update(1, value))
    assert copy.KEEP not in [b.text for row in screen(responses).reply_markup.inline_keyboard for b in row]
    responses = await ui(message_update(1, "Адрес"))
    assert "Алексей" not in screen(responses).text
    assert "Борис" in [b.text for row in screen(responses).reply_markup.inline_keyboard for b in row]
    responses = await click(ui, responses, "Борис")
    responses = await click(ui, responses, "Айдана")
    responses = await click(ui, responses, copy.SKIP)
    await click(ui, responses, copy.CREATE)
    async with factory() as session:
        assert (await session.scalar(select(Order))).courier_id == ids[3]


async def test_participant_deactivated_after_summary_prevents_creation(ui, prepared):
    _, factory, ids = prepared
    responses = await draft_summary(ui)
    async with factory.begin() as session:
        await session.execute(update(User).where(User.id == ids[1]).values(active=False))
    responses = await click(ui, responses, copy.CREATE)
    assert responses[0].text == copy.INVALID_PARTICIPANT
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 0
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 0


@pytest.mark.parametrize("telegram_id", [2, 3, 99])
@pytest.mark.parametrize("action", ["admin:orders", "admin:settings", "ops:new", "ops:set:waiting_minutes", "ops:menu", "form:fake:confirm"])
async def test_every_creation_action_requires_administrator(ui, telegram_id, action):
    responses = await ui(callback_update(telegram_id, action))
    assert len(responses) == 1 and responses[0].text == texts.ACCESS_DENIED


async def test_revoked_administrator_cannot_submit_text_or_confirmation(ui, prepared):
    _, factory, ids = prepared
    responses = await draft_summary(ui)
    value = button(responses, copy.CREATE)
    async with factory.begin() as session:
        await session.execute(update(User).where(User.id == ids[0]).values(active=False))
    assert (await ui(callback_update(1, value)))[0].text == texts.ACCESS_DENIED
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 0


async def test_services_reject_wrong_role_and_rollback_failed_audit(prepared, monkeypatch):
    _, factory, ids = prepared
    async with factory.begin() as session:
        with pytest.raises(AccessDenied):
            await create_order(session, 2, draft(ids))
        with pytest.raises(AccessDenied):
            await save_default(session, 2, "waiting_minutes", "20")
        with pytest.raises(InvalidDraft):
            await create_order(session, 1, dict(draft(ids), courier_id=ids[2]))
        with pytest.raises(InvalidDraft):
            await save_default(session, 1, "courier_id", ids[2])
    from app.services import orders
    async def fail(*args, **kwargs):
        raise RuntimeError("audit failed")
    monkeypatch.setattr(orders, "append_event", fail)
    async with factory.begin() as session:
        with pytest.raises(RuntimeError):
            await create_order(session, 1, draft(ids))
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 0


@pytest.mark.parametrize("field,value", [("name", "Другой заказ"), ("date", "16.10.2026"),
    ("time", "16:45"), ("pickup_address", "Новая точка"), ("product_description", "Коробка"),
    ("courier_id", "Борис"), ("customer_id", "Дина")])
async def test_each_summary_field_can_be_changed(ui, field, value):
    responses = await draft_summary(ui)
    responses = await click(ui, responses, copy.EDIT)
    responses = await click(ui, responses, copy.LABELS[field])
    responses = (await click(ui, responses, value) if field.endswith("_id")
                 else await ui(message_update(1, value)))
    assert f"{copy.LABELS[field]}: {value}" in screen(responses).text
    assert screen(await click(ui, responses, copy.CREATE)).text == copy.CREATED


async def test_settings_invalid_cancel_and_revoked_input(ui, prepared):
    _, factory, ids = prepared
    responses = await ui(callback_update(1, "ops:set:waiting_minutes"))
    assert (await ui(message_update(1, "0")))[-1].text.startswith(copy.INVALID)
    await click(ui, responses, texts.CANCEL)
    async with factory() as session:
        assert (await session.get(Defaults, 1)).waiting_minutes == 15
    await ui(callback_update(1, "ops:set:pickup_address"))
    async with factory.begin() as session:
        await session.execute(update(User).where(User.id == ids[0]).values(active=False))
    assert (await ui(message_update(1, "Нельзя сохранить")))[0].text == texts.ACCESS_DENIED
    async with factory() as session:
        assert (await session.get(Defaults, 1)).pickup_address == "Бишкек, адрес выдачи"


async def test_no_participants_and_paginated_selection(ui, prepared):
    _, factory, _ = prepared
    async with factory.begin() as session:
        await session.execute(update(User).where(User.role == Role.COURIER).values(active=False))
    responses = await ui(callback_update(1, "ops:set:courier_id"))
    assert copy.NO_CHOICES in screen(responses).text
    assert [row[0].text for row in screen(responses).reply_markup.inline_keyboard] == [texts.CANCEL]
    async with factory.begin() as session:
        session.add_all([User(telegram_user_id=100 + i, role=Role.COURIER, display_name=f"Курьер {i}")
                         for i in range(11)])
    responses = await ui(callback_update(1, "ops:set:courier_id"))
    responses = await click(ui, responses, texts.NEXT)
    responses = await click(ui, responses, "Курьер 10")
    assert "Курьер 10" in screen(responses).text


async def test_failed_telegram_delivery_does_not_duplicate_creation(prepared, token):
    _, factory, _ = prepared
    dispatcher = create_dispatcher(sessions=factory)
    async with Bot(token).context() as bot:
        bot.session.make_request = AsyncMock(return_value=True)
        async def send(event):
            bot.session.make_request.reset_mock()
            await dispatcher.feed_update(bot, event)
            return [call.args[1] for call in bot.session.make_request.call_args_list]
        responses = await draft_summary(send)
        confirmation = button(responses, copy.CREATE)
        from aiogram.exceptions import TelegramNetworkError
        async def fail_delivery(bot, method, **kwargs):
            if isinstance(method, EditMessageText):
                raise TelegramNetworkError(method=method, message="delivery failed")
            return True
        bot.session.make_request.side_effect = fail_delivery
        await send(callback_update(1, confirmation))
        responses = await send(callback_update(1, confirmation))
        assert responses[0].text == texts.STALE_ACTION
    await dispatcher.fsm.close()
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 1
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 1
