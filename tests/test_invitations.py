import asyncio
import re
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import GetMe
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select, update

from app.bot import texts
from app.bot.handlers import create_dispatcher
from app.db.models import Invitation, Role, User
from app.services.authorization import AccessDenied
from app.services.invitations import InvalidInvitation, RoleConflict, create_invitation, redeem_invitation, token_hash
from test_persistence import database
from test_registration import callback_update, message_update


@pytest.fixture
async def admin_database(database):
    _, factory, _ = database
    async with factory.begin() as session:
        session.add(User(telegram_user_id=1, role=Role.ADMINISTRATOR))
    return factory


async def invite(factory, role=Role.COURIER, name="Алексей"):
    async with factory.begin() as session:
        return await create_invitation(session, administrator_id=1, role=role, name=name)


async def redeem(factory, value, telegram_id=2):
    async with factory.begin() as session:
        return await redeem_invitation(session, token=value, telegram_user_id=telegram_id,
                                       username="participant", display_name="Имя в Telegram")


@pytest.mark.parametrize("role", [Role.COURIER, Role.CUSTOMER])
async def test_invite_roundtrip_hash_and_identity(admin_database, role):
    factory = admin_database
    token = await invite(factory, role)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    assert len("invite_" + token) <= 64
    async with factory() as session:
        invitation = await session.scalar(select(Invitation))
        assert invitation.token_hash == token_hash(token)
        assert token not in invitation.token_hash
        assert invitation.used_at is None
    participant = await redeem(factory, token)
    async with factory() as session:
        user = await session.get(User, participant.id)
        invitation = await session.scalar(select(Invitation))
        assert user.role == role and user.active
        assert user.telegram_user_id == 2
        assert user.display_name == "Алексей"
        assert user.username == "participant"
        assert user.telegram_display_name == "Имя в Telegram"
        assert invitation.participant_id == user.id
        assert invitation.used_at is not None
    with pytest.raises(InvalidInvitation):
        await redeem(factory, token, 3)


@pytest.mark.parametrize("value", ["", "bad", "A" * 43, "я" * 43, "../" * 20])
async def test_invalid_token_does_not_register(admin_database, value):
    with pytest.raises(InvalidInvitation):
        await redeem(admin_database, value)
    async with admin_database() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_revoked_invitation_rejected(admin_database):
    value = await invite(admin_database)
    async with admin_database.begin() as session:
        await session.execute(update(Invitation).values(revoked=True))
    with pytest.raises(InvalidInvitation):
        await redeem(admin_database, value)


async def test_existing_role_does_not_consume_invite(admin_database):
    value = await invite(admin_database)
    with pytest.raises(RoleConflict):
        await redeem(admin_database, value, 1)
    async with admin_database() as session:
        assert (await session.scalar(select(Invitation))).used_at is None
    assert (await redeem(admin_database, value, 2)).role == Role.COURIER


async def test_concurrent_redemption_only_one_winner(admin_database):
    value = await invite(admin_database)
    results = await asyncio.gather(redeem(admin_database, value, 2),
                                   redeem(admin_database, value, 3), return_exceptions=True)
    assert sum(isinstance(result, User) for result in results) == 1
    assert sum(isinstance(result, InvalidInvitation) for result in results) == 1


async def test_two_invites_same_account_create_only_one_active_role(admin_database):
    first = await invite(admin_database, Role.COURIER)
    second = await invite(admin_database, Role.CUSTOMER)
    results = await asyncio.gather(redeem(admin_database, first), redeem(admin_database, second),
                                   return_exceptions=True)
    assert sum(isinstance(result, User) for result in results) == 1
    assert sum(isinstance(result, RoleConflict) for result in results) == 1
    async with admin_database() as session:
        assert await session.scalar(select(func.count()).select_from(Invitation).where(Invitation.used_at.is_not(None))) == 1


async def test_failed_binding_rolls_back_invitation_claim(admin_database, monkeypatch):
    value = await invite(admin_database)
    async with admin_database.begin() as session:
        # Simulate a failed INSERT after the invitation was claimed.
        original = session.flush

        async def fail_flush(*args, **kwargs):
            if any(isinstance(item, User) for item in session.new):
                raise RuntimeError("binding failed")
            return await original(*args, **kwargs)

        monkeypatch.setattr(session, "flush", fail_flush)
        with pytest.raises(RuntimeError):
            await redeem_invitation(session, token=value, telegram_user_id=2)
    async with admin_database() as session:
        assert (await session.scalar(select(Invitation))).used_at is None
        assert await session.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, "Имя\nЕщё"])
async def test_invalid_participant_names(admin_database, name):
    with pytest.raises(ValueError):
        await invite(admin_database, name=name)


async def test_invitation_creation_requires_admin_and_participant_role(admin_database):
    async with admin_database.begin() as session:
        with pytest.raises(AccessDenied):
            await create_invitation(session, administrator_id=999, role=Role.COURIER, name="Имя")
        with pytest.raises(ValueError):
            await create_invitation(session, administrator_id=1, role=Role.ADMINISTRATOR, name="Имя")


@pytest.fixture
async def conversation(admin_database, token):
    dispatcher = create_dispatcher(sessions=admin_database)
    async with Bot(token).context() as bot:
        async def response(bot, method, **kwargs):
            if isinstance(method, GetMe):
                return TelegramUser(id=123456789, is_bot=True, first_name="Бот", username="test_handover_bot")
            return True
        bot.session.make_request = AsyncMock(side_effect=response)

        async def send(event):
            bot.session.make_request.reset_mock()
            await dispatcher.feed_update(bot, event)
            return [call.args[1] for call in bot.session.make_request.call_args_list]
        yield send
    await dispatcher.fsm.close()


@pytest.mark.parametrize("role,prompt", [(Role.COURIER, texts.COURIER_NAME), (Role.CUSTOMER, texts.CUSTOMER_NAME)])
async def test_admin_name_link_and_participant_registration_flow(conversation, admin_database, role, prompt):
    send = conversation
    responses = await send(callback_update(1, "admin:participants"))
    assert responses[-1].text == texts.NO_PARTICIPANTS
    responses = await send(callback_update(1, f"participants:add:{role.value}"))
    assert responses[-1].text == prompt
    responses = await send(message_update(1, "  Алексей  "))
    link_message = responses[-1].text
    assert "Алексей" in link_message
    value = re.search(r"start=invite_([A-Za-z0-9_-]{43})", link_message).group(1)
    responses = await send(message_update(2, f"/start invite_{value}"))
    assert responses[-1].text.startswith("Регистрация завершена.")
    assert "Алексей" in responses[-1].text
    responses = await send(message_update(2))
    assert "Ваш профиль:" in responses[-1].text
    assert (texts.COURIER_GUIDANCE if role == Role.COURIER else texts.CUSTOMER_GUIDANCE) in responses[-1].text
    responses = await send(callback_update(1, "admin:participants"))
    assert "Алексей" in responses[-1].reply_markup.inline_keyboard[2][0].text
    # Duplicate name delivery after completion must not issue a second link.
    await send(message_update(1, "Алексей"))
    async with admin_database() as session:
        assert await session.scalar(select(func.count()).select_from(Invitation)) == 1


async def test_name_validation_cancel_and_revoked_admin(conversation, admin_database):
    send = conversation
    await send(callback_update(1, "participants:add:COURIER"))
    responses = await send(message_update(1, "   "))
    assert responses[-1].text == texts.INVALID_PARTICIPANT_NAME
    await send(callback_update(1, "participants:cancel"))
    await send(message_update(1, "Имя"))
    async with admin_database() as session:
        assert await session.scalar(select(func.count()).select_from(Invitation)) == 0
    await send(callback_update(1, "participants:add:CUSTOMER"))
    async with admin_database.begin() as session:
        await session.execute(update(User).where(User.telegram_user_id == 1).values(active=False))
    responses = await send(message_update(1, "Имя"))
    assert responses[-1].text == texts.ACCESS_DENIED


@pytest.mark.parametrize("action", ["participants:add:COURIER", "participants:add:CUSTOMER",
                                    "participants:view:1", "participants:page:0"])
async def test_participant_actions_are_admin_only(conversation, action):
    responses = await conversation(callback_update(9, action))
    assert responses[-1].text == texts.ACCESS_DENIED


async def test_invalid_deep_links_do_not_offer_admin_registration(conversation):
    responses = await conversation(message_update(2, "/start invite_invalid"))
    assert responses[-1].text == texts.INVALID_INVITATION
    assert responses[-1].reply_markup is None
