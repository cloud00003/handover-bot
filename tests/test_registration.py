import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Update
from sqlalchemy import func, select, update

from app.bot import texts
from app.bot.handlers import create_dispatcher
from app.bot.registration import CONFIRM_ADMIN
from app.db.models import Order, OrderStatus, Role, User
from app.services.authorization import AccessDenied, require_role
from app.services.registration import is_initialized, register_first_administrator
from test_persistence import database, seed  # shared migrated, temporary SQLite fixture


def message_update(user_id, text="/start", username="example"):
    user = {"id": user_id, "is_bot": False, "first_name": "Анна", "last_name": "Иванова"}
    if username is not None:
        user["username"] = username
    return Update.model_validate({"update_id": user_id, "message": {
        "message_id": 1, "date": datetime.now(timezone.utc), "from": user,
        "chat": {"id": user_id, "type": "private"}, "text": text,
        "entities": ([{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
                     if text.startswith("/") else []),
    }})


def callback_update(user_id, data=CONFIRM_ADMIN, username="example"):
    message = message_update(user_id, username=username).message
    return Update(update_id=user_id, callback_query={
        "id": f"query-{user_id}", "from": message.from_user,
        "chat_instance": "chat", "message": message, "data": data,
    })


async def dispatch(factory, token, event):
    async with Bot(token).context() as bot:
        bot.session.make_request = AsyncMock(return_value=True)
        await create_dispatcher(sessions=factory).feed_update(bot, event)
        return [call.args[1] for call in bot.session.make_request.call_args_list]


async def test_start_then_confirm_persists_identity_and_menu(database, token):
    engine, factory, _ = database
    responses = await dispatch(factory, token, message_update(1))
    assert len(responses) == 1 and isinstance(responses[0], SendMessage)
    assert responses[0].text == texts.WELCOME
    assert responses[0].text.startswith("👋 Добро пожаловать в Handover Bot!")
    assert "передачу товара между курьером и заказчиком" in responses[0].text
    assert "подтверждение получения" in responses[0].text
    assert "Система ещё не настроена." in responses[0].text
    assert responses[0].text.endswith("подтвердите роль администратора.")
    buttons = responses[0].reply_markup.inline_keyboard
    assert [[b.text for b in row] for row in buttons] == [[texts.CONFIRM_ADMIN]]
    assert buttons[0][0].callback_data == CONFIRM_ADMIN
    async with factory() as session:
        assert not await is_initialized(session)
        assert await session.scalar(select(func.count()).select_from(User)) == 0
    before = datetime.now(timezone.utc)
    responses = await dispatch(factory, token, callback_update(1))
    assert isinstance(responses[0], AnswerCallbackQuery)
    assert responses[0].text == texts.ADMIN_REGISTERED
    assert isinstance(responses[1], EditMessageText)
    assert responses[1].text == texts.ADMIN_MENU_GUIDANCE
    assert [row[0].text for row in responses[1].reply_markup.inline_keyboard] == [
        "Заказы", "Участники", "История", "Настройки"]
    await engine.dispose()
    async with factory() as session:
        assert await is_initialized(session)
        user = await require_role(session, 1, Role.ADMINISTRATOR)
        assert user.username == "example"
        assert user.telegram_display_name == "Анна Иванова"
        assert user.active
        assert before <= user.registered_at <= datetime.now(timezone.utc)
    responses = await dispatch(factory, token, message_update(1))
    assert responses[0].text == texts.ADMIN_MENU_GUIDANCE
    assert responses[0].text.startswith("👋 Добро пожаловать!")
    assert "Вы вошли как администратор." in responses[0].text
    for guidance in ("создавать и просматривать заказы", "управлять курьерами и заказчиками",
                     "просматривать историю передач", "изменять основные настройки"):
        assert guidance in responses[0].text
    assert responses[0].text.endswith("Выберите нужный раздел.")
    assert [[(b.text, b.callback_data) for b in row]
            for row in responses[0].reply_markup.inline_keyboard] == [
        [("Заказы", "admin:orders")], [("Участники", "admin:participants")],
        [("История", "admin:history")], [("Настройки", "admin:settings")]]


async def test_second_user_stale_and_duplicate_confirmation_cannot_register(database, token):
    _, factory, _ = database
    await dispatch(factory, token, message_update(2))  # obtained a button before initialization
    await dispatch(factory, token, callback_update(1))
    for user_id in (2, 1):
        responses = await dispatch(factory, token, callback_update(user_id))
        assert responses[0].text == texts.ADMIN_REGISTRATION_CLOSED
    responses = await dispatch(factory, token, message_update(2))
    assert responses[0].text == texts.ACCESS_DENIED
    assert responses[0].reply_markup is None
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize("ids", [(1, 2), (1, 1)])
async def test_concurrent_registration_has_one_winner(database, ids):
    _, factory, _ = database

    async def confirm(user_id):
        async with factory.begin() as session:
            return await register_first_administrator(session, telegram_user_id=user_id)

    assert sorted(await asyncio.gather(*(confirm(i) for i in ids))) == [False, True]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        user = await session.scalar(select(User))
        assert user.username is None
        assert user.telegram_display_name is None


async def test_inactive_administrator_does_not_reopen_registration(database, token):
    _, factory, _ = database
    await dispatch(factory, token, callback_update(1, username=None))
    async with factory.begin() as session:
        await session.execute(update(User).values(active=False))
    for user_id in (1, 2):
        responses = await dispatch(factory, token, message_update(user_id))
        assert responses[0].text == texts.ACCESS_DENIED
        assert responses[0].reply_markup is None
        responses = await dispatch(factory, token, callback_update(user_id))
        assert responses[0].text == texts.ADMIN_REGISTRATION_CLOSED


@pytest.mark.parametrize("role,active", [(None, False), (Role.COURIER, True),
                                        (Role.CUSTOMER, True), (Role.ADMINISTRATOR, False)])
@pytest.mark.parametrize("action", ["orders", "participants", "history", "settings"])
async def test_every_admin_menu_action_requires_current_active_admin(database, token, role, active, action):
    _, factory, _ = database
    if role:
        async with factory.begin() as session:
            session.add(User(telegram_user_id=2, username="same_username", role=role, active=active))
    responses = await dispatch(factory, token, callback_update(2, f"admin:{action}"))
    assert len(responses) == 1
    assert responses[0].text == texts.ACCESS_DENIED
    assert responses[0].show_alert


async def test_cached_menu_does_not_keep_authorization_after_deactivation(database, token):
    _, factory, _ = database
    await dispatch(factory, token, callback_update(1))
    responses = await dispatch(factory, token, callback_update(1, "admin:history"))
    assert isinstance(responses[-1], EditMessageText)
    assert "Завершённых передач пока нет." in responses[-1].text
    async with factory.begin() as session:
        await session.execute(update(User).values(active=False))
    responses = await dispatch(factory, token, callback_update(1, "admin:history"))
    assert responses[0].text == texts.ACCESS_DENIED


async def test_authorization_uses_id_not_username_and_enforces_role(database):
    _, factory, _ = database
    async with factory.begin() as session:
        session.add_all([User(telegram_user_id=1, username="shared", role=Role.ADMINISTRATOR),
                         User(telegram_user_id=2, username="shared", role=Role.CUSTOMER)])
    async with factory() as session:
        assert (await require_role(session, 1, Role.ADMINISTRATOR)).telegram_user_id == 1
        for user_id, role in ((2, Role.ADMINISTRATOR), (2, Role.COURIER), (3, Role.ADMINISTRATOR)):
            with pytest.raises(AccessDenied):
                await require_role(session, user_id, role)


async def test_existing_active_participant_cannot_get_second_role(database):
    _, factory, _ = database
    async with factory.begin() as session:
        session.add(User(telegram_user_id=1, role=Role.CUSTOMER))
    async with factory.begin() as session:
        assert not await register_first_administrator(session, telegram_user_id=1)
    async with factory() as session:
        assert (await session.scalar(select(User))).role == Role.CUSTOMER


async def test_no_secret_command_registers_user(database, token):
    _, factory, _ = database
    await dispatch(factory, token, message_update(1, text="/admin"))
    async with factory() as session:
        assert not await is_initialized(session)


async def test_concurrent_confirmation_callbacks_have_one_success(database, token):
    _, factory, _ = database
    responses = await asyncio.gather(
        dispatch(factory, token, callback_update(1)),
        dispatch(factory, token, callback_update(2)),
    )
    assert sorted(result[0].text for result in responses) == sorted([
        texts.ADMIN_REGISTERED, texts.ADMIN_REGISTRATION_CLOSED])
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_registration_rollback_does_not_initialize_system(database):
    _, factory, _ = database
    with pytest.raises(RuntimeError):
        async with factory.begin() as session:
            assert await register_first_administrator(session, telegram_user_id=1)
            raise RuntimeError("transaction failed")
    async with factory() as session:
        assert not await is_initialized(session)
    async with factory.begin() as session:
        assert await register_first_administrator(session, telegram_user_id=2)


@pytest.mark.parametrize("role,telegram_id", [(Role.COURIER, 200), (Role.CUSTOMER, 300)])
@pytest.mark.parametrize("order_state", ["active", "completed", "other_account"])
async def test_participant_start_guidance_and_accurate_empty_state(database, token, role, telegram_id, order_state):
    _, factory, _ = database
    _, _, _, order_id = await seed(factory)
    if order_state == "completed":
        async with factory.begin() as session:
            await session.execute(update(Order).where(Order.id == order_id).values(_status=OrderStatus.COMPLETED))
    elif order_state == "other_account":
        telegram_id = 400
        async with factory.begin() as session:
            session.add(User(telegram_user_id=telegram_id, role=role, display_name="Участник"))
    responses = await dispatch(factory, token, message_update(telegram_id))
    assert len(responses) == 1
    guidance = texts.COURIER_GUIDANCE if role == Role.COURIER else texts.CUSTOMER_GUIDANCE
    empty = texts.COURIER_NO_ORDER if role == Role.COURIER else texts.CUSTOMER_NO_ORDER
    assert responses[0].text.startswith(guidance)
    assert (empty in responses[0].text) == (order_state != "active")
    if role == Role.COURIER and order_state == "active":
        assert responses[0].reply_markup.inline_keyboard[0][0].callback_data == f"courier:order:{order_id}"
    else:
        assert responses[0].reply_markup is None
    assert ("Вы вошли как курьер." if role == Role.COURIER else "Вы вошли как заказчик.") in responses[0].text


@pytest.mark.parametrize("role", [Role.COURIER, Role.CUSTOMER])
async def test_inactive_participant_start_does_not_show_guidance(database, token, role):
    _, factory, _ = database
    async with factory.begin() as session:
        session.add_all([User(telegram_user_id=1, role=Role.ADMINISTRATOR),
                         User(telegram_user_id=2, role=role, active=False)])
    responses = await dispatch(factory, token, message_update(2))
    assert responses[0].text == texts.ACCESS_DENIED
    assert responses[0].reply_markup is None


@pytest.mark.parametrize("section", ["orders", "participants", "history", "settings"])
async def test_main_menu_guidance_after_section_navigation(database, token, section):
    _, factory, _ = database
    async with factory.begin() as session:
        await register_first_administrator(session, telegram_user_id=1)
    initial = (await dispatch(factory, token, message_update(1)))[0]
    event = callback_update(1, f"admin:{section}")
    event = event.model_copy(update={"callback_query": event.callback_query.model_copy(update={
        "message": event.callback_query.message.model_copy(update={
            "text": initial.text, "reply_markup": initial.reply_markup})})})
    responses = await dispatch(factory, token, event)
    if section in ("participants", "orders", "settings", "history"):
        submenu = next(response for response in responses if isinstance(response, EditMessageText))
        back = submenu.reply_markup.inline_keyboard[-1][0]
        assert back.text == "Главное меню"
        event = callback_update(1, back.callback_data)
        event = event.model_copy(update={"callback_query": event.callback_query.model_copy(update={
            "message": event.callback_query.message.model_copy(update={
                "text": submenu.text, "reply_markup": submenu.reply_markup})})})
        responses = await dispatch(factory, token, event)
        assert len(responses) == 2
        assert isinstance(responses[0], AnswerCallbackQuery)
        assert isinstance(responses[1], EditMessageText)
        assert responses[1].text == texts.ADMIN_MENU_RETURN
        assert responses[1].text.startswith("Главное меню\n\n")
        assert "Добро пожаловать" not in responses[1].text
        assert "Вы вошли как администратор" not in responses[1].text
        assert texts.ADMIN_MENU_ORIENTATION in responses[1].text
        assert responses[1].reply_markup == initial.reply_markup
    else:
        # These sections only show an alert; dismissing it leaves the menu intact.
        assert len(responses) == 1
        assert isinstance(responses[0], AnswerCallbackQuery)
        assert responses[0].text == texts.SECTION_UNAVAILABLE
        assert responses[0].show_alert
        assert event.callback_query.message.text == texts.ADMIN_MENU_GUIDANCE
        assert event.callback_query.message.reply_markup == initial.reply_markup
    assert not any(isinstance(response, SendMessage) for response in responses)


@pytest.mark.parametrize("role,active", [(None, False), (Role.COURIER, True),
                                       (Role.CUSTOMER, True), (Role.ADMINISTRATOR, False)])
async def test_main_menu_return_requires_active_administrator(database, token, role, active):
    _, factory, _ = database
    if role is not None:
        async with factory.begin() as session:
            session.add(User(telegram_user_id=2, role=role, active=active))
    responses = await dispatch(factory, token, callback_update(2, "participants:menu"))
    assert len(responses) == 1
    assert responses[0].text == texts.ACCESS_DENIED
    assert isinstance(responses[0], AnswerCallbackQuery)
