import asyncio
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, StatementError

from app.db.base import utc_now
from app.db.models import Defaults, Invitation, Order, OrderEvent, OrderStatus as S, PickupCode, Role, User
from app.db.repositories import consume_code_record, consume_invitation, replace_code_record
from app.db.session import create_engine, session_factory
from app.services.orders import StaleOrder, record_order, transition_order
from app.services.state_machine import InvalidTransition, validate_transition
from app.time import scheduled_utc


def migration_config(url):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@pytest.fixture
async def database(tmp_path):
    url = "sqlite+aiosqlite:///" + (tmp_path / "test.sqlite3").as_posix()
    config = migration_config(url)
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_engine(url)
    try:
        yield engine, session_factory(engine), config
    finally:
        await engine.dispose()


async def seed(factory):
    async with factory.begin() as session:
        admin = User(telegram_user_id=100, role=Role.ADMINISTRATOR)
        courier = User(telegram_user_id=200, role=Role.COURIER, display_name="Курьер")
        customer = User(telegram_user_id=300, role=Role.CUSTOMER, display_name="Заказчик")
        session.add_all([admin, courier, customer])
        await session.flush()
        order = Order(name="Заказ", scheduled_at=utc_now(), pickup_address="Адрес",
                      courier_id=courier.id, customer_id=customer.id, waiting_minutes=15)
        session.add(order)
        await session.flush()
        return admin.id, courier.id, customer.id, order.id


async def test_migration_defaults_schema_and_roundtrip(database):
    engine, factory, config = database
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
        assert set(tables) == {"alembic_version", "users", "invitations", "orders",
                               "pickup_codes", "defaults", "order_events"}
        assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
    async with factory() as session:
        defaults = await session.get(Defaults, 1)
        assert defaults.waiting_minutes == 15
        assert defaults.code_lifetime_minutes == 20
        assert await session.scalar(select(func.count()).select_from(User)) == 0
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)
    await engine.dispose()
    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")


async def test_order_survives_restart_and_unbinding(database):
    engine, factory, config = database
    _, courier_id, _, order_id = await seed(factory)
    before = utc_now()
    async with factory.begin() as session:
        await transition_order(session, order_id=order_id, expected_status=S.SCHEDULED,
                               target_status=S.COURIER_ARRIVED,
                               actor_telegram_user_id=200, actor_role=Role.COURIER)
        await session.execute(update(User).where(User.id == courier_id).values(active=False))
        session.add(User(telegram_user_id=200, role=Role.CUSTOMER, display_name="Новая роль"))
    await engine.dispose()
    restarted = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        async with session_factory(restarted)() as session:
            order = await session.get(Order, order_id)
            event = await session.scalar(select(OrderEvent))
            assert order.status == S.COURIER_ARRIVED
            assert order.courier_id == courier_id
            assert before <= order.arrived_at <= utc_now()
            assert order.arrived_at == event.occurred_at
            assert event.actor_telegram_user_id == 200
            assert event.actor_role == Role.COURIER
            assert event.details == {"previous_status": "SCHEDULED", "new_status": "COURIER_ARRIVED"}
            assert (await session.get(User, courier_id)).display_name == "Курьер"
    finally:
        await restarted.dispose()


@pytest.mark.parametrize("role,telegram_id", [(Role.CUSTOMER, 200), (Role.ADMINISTRATOR, 400)])
async def test_unique_active_identity_and_administrator(database, role, telegram_id):
    _, factory, _ = database
    await seed(factory)
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            session.add(User(telegram_user_id=telegram_id, role=role))


async def test_referenced_participant_cannot_be_deleted(database):
    _, factory, _ = database
    _, courier_id, _, _ = await seed(factory)
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            await session.execute(delete(User).where(User.id == courier_id))


@pytest.mark.parametrize("operation", ["update", "delete", "replace"])
async def test_events_immutable_even_via_sql(database, operation):
    _, factory, _ = database
    *_, order_id = await seed(factory)
    async with factory.begin() as session:
        session.add(OrderEvent(order_id=order_id, event_type="TEST"))
    with pytest.raises(IntegrityError, match="immutable"):
        async with factory.begin() as session:
            statements = {
                "update": "UPDATE order_events SET event_type = 'CHANGED'",
                "delete": "DELETE FROM order_events",
                "replace": "INSERT OR REPLACE INTO order_events (id, order_id, event_type) "
                           "SELECT id, order_id, 'CHANGED' FROM order_events",
            }
            await session.execute(text(statements[operation]))


async def test_transition_and_event_rollback_together(database):
    _, factory, _ = database
    *_, order_id = await seed(factory)
    async with factory.begin() as session:
        with pytest.raises(StatementError):
            await transition_order(session, order_id=order_id, expected_status=S.SCHEDULED,
                                   target_status=S.COURIER_ARRIVED, metadata={"bad": object()})
    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order.status == S.SCHEDULED
        assert order.arrived_at is None
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 0


async def test_outer_rollback_reverts_state_and_event(database):
    _, factory, _ = database
    *_, order_id = await seed(factory)
    with pytest.raises(RuntimeError):
        async with factory.begin() as session:
            await transition_order(session, order_id=order_id, expected_status=S.SCHEDULED,
                                   target_status=S.COURIER_ARRIVED)
            raise RuntimeError("caller failed")
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == S.SCHEDULED
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 0


async def test_concurrent_arrivals_record_one_event(database):
    _, factory, _ = database
    *_, order_id = await seed(factory)

    async def arrive():
        try:
            async with factory.begin() as session:
                await transition_order(session, order_id=order_id, expected_status=S.SCHEDULED,
                                       target_status=S.COURIER_ARRIVED)
            return True
        except StaleOrder:
            return False

    assert sorted(await asyncio.gather(arrive(), arrive())) == [False, True]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 1


@pytest.mark.parametrize("previous,target", [
    (S.SCHEDULED, S.READY_FOR_PICKUP), (S.SCHEDULED, S.COMPLETED),
    (S.COMPLETED, S.CUSTOMER_NO_SHOW), (S.CANCELLED, S.COURIER_ARRIVED),
    (S.CUSTOMER_NO_SHOW, S.COMPLETED), (S.DISPUTED, S.COMPLETED),
    (S.COURIER_ARRIVED, S.COURIER_ARRIVED),
])
def test_invalid_transitions(previous, target):
    with pytest.raises(InvalidTransition):
        validate_transition(previous, target)


@pytest.mark.parametrize("target", [s for s in S if s not in (S.COMPLETED, S.DISPUTED)])
def test_verified_handover_only_allows_completion_or_dispute(target):
    with pytest.raises(InvalidTransition):
        validate_transition(S.AWAITING_CUSTOMER_CONFIRMATION, target)


async def test_happy_state_path_and_timestamps(database):
    _, factory, _ = database
    *_, order_id = await seed(factory)
    states = [S.SCHEDULED, S.COURIER_ARRIVED, S.LOCATION_SUBMITTED, S.PHOTO_SUBMITTED,
              S.READY_FOR_PICKUP, S.AWAITING_CODE_CONFIRMATION,
              S.AWAITING_CUSTOMER_CONFIRMATION, S.COMPLETED]
    async with factory.begin() as session:
        for previous, target in zip(states, states[1:]):
            await transition_order(session, order_id=order_id, expected_status=previous,
                                   target_status=target)
        order = await session.get(Order, order_id)
        assert order.completed_at == order.customer_confirmed_at
        assert order.created_at <= order.arrived_at <= order.ready_at <= order.completed_at
        with pytest.raises(AttributeError):
            order.status = S.SCHEDULED


async def test_invitation_single_use_revocation_and_concurrency(database):
    _, factory, _ = database
    admin, *_ = await seed(factory)
    async with factory.begin() as session:
        invite = Invitation(token_hash="hash", intended_role=Role.COURIER,
                            display_name="Имя", invited_by_id=admin)
        session.add(invite)
        await session.flush()
        invite_id = invite.id

    async def consume():
        async with factory.begin() as session:
            return await consume_invitation(session, invite_id)

    assert sorted(await asyncio.gather(consume(), consume())) == [False, True]
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            await session.execute(update(Invitation).where(Invitation.id == invite_id).values(used_at=None))
    async with factory.begin() as session:
        revoked = Invitation(token_hash="revoked", intended_role=Role.CUSTOMER,
                             display_name="Имя", invited_by_id=admin, revoked=True)
        session.add(revoked)
        await session.flush()
        assert not await consume_invitation(session, revoked.id)


async def test_code_lifetime_replacement_expiry_and_consumption(database, monkeypatch):
    from app.db import repositories

    _, factory, _ = database
    *_, order_id = await seed(factory)
    now = utc_now()
    monkeypatch.setattr(repositories, "utc_now", lambda: now)
    async with factory.begin() as session:
        first = await replace_code_record(session, order_id=order_id, code_hash="first-hash")
        assert first.expires_at - first.generated_at == timedelta(minutes=20)
        second = await replace_code_record(session, order_id=order_id, code_hash="second-hash")
        assert not await consume_code_record(session, code_id=first.id, order_id=order_id)
        assert not await consume_code_record(session, code_id=second.id, order_id=order_id + 1)
        monkeypatch.setattr(repositories, "utc_now", lambda: now + timedelta(minutes=20))
        assert not await consume_code_record(session, code_id=second.id, order_id=order_id)
        third = await replace_code_record(session, order_id=order_id, code_hash="third-hash")
        assert await consume_code_record(session, code_id=third.id, order_id=order_id)
        assert not await consume_code_record(session, code_id=third.id, order_id=order_id)
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            await session.execute(update(PickupCode).where(PickupCode.id == third.id).values(used_at=None))


async def test_only_one_active_code_and_replacement_rollback(database):
    _, factory, _ = database
    *_, order_id = await seed(factory)
    async with factory.begin() as session:
        first = await replace_code_record(session, order_id=order_id, code_hash="hash")
        first_id = first.id
        with pytest.raises(IntegrityError):
            await replace_code_record(session, order_id=order_id, code_hash="")
    async with factory() as session:
        assert (await session.get(PickupCode, first_id)).invalidated_at is None
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            session.add(PickupCode(order_id=order_id, code_hash="duplicate",
                                   generated_at=utc_now(), expires_at=utc_now() + timedelta(minutes=20)))


async def test_utc_storage_and_naive_datetime_rejection(database):
    _, factory, _ = database
    *_, order_id = await seed(factory)
    local = datetime(2026, 9, 8, 18, tzinfo=timezone(timedelta(hours=5)))
    async with factory.begin() as session:
        await session.execute(update(Order).where(Order.id == order_id).values(scheduled_at=local))
    async with factory() as session:
        assert (await session.get(Order, order_id)).scheduled_at == datetime(2026, 9, 8, 13, tzinfo=timezone.utc)
    with pytest.raises(StatementError):
        async with factory.begin() as session:
            await session.execute(update(Order).where(Order.id == order_id).values(scheduled_at=datetime(2026, 9, 8)))


async def test_creation_is_audited_and_event_failure_rolls_back(database, monkeypatch):
    from app.services import orders

    _, factory, _ = database
    _, courier_id, customer_id, _ = await seed(factory)
    async with factory.begin() as session:
        order = Order(name="Новый заказ", scheduled_at=utc_now(), pickup_address="Адрес",
                      courier_id=courier_id, customer_id=customer_id)
        event = await record_order(session, order, actor_telegram_user_id=100,
                                   actor_role=Role.ADMINISTRATOR)
        assert order.created_at == event.occurred_at
        assert event.event_type == "ORDER_CREATED"
        assert order.status == S.SCHEDULED
        with pytest.raises(ValueError):
            await record_order(session, order, actor_telegram_user_id=100,
                               actor_role=Role.ADMINISTRATOR)
    async with factory.begin() as session:
        async def fail(*args, **kwargs):
            raise RuntimeError("audit failure")

        monkeypatch.setattr(orders, "append_event", fail)
        with pytest.raises(RuntimeError):
            await record_order(session, Order(name="Не сохранять", scheduled_at=utc_now(),
                               pickup_address="Адрес", courier_id=courier_id,
                               customer_id=customer_id), actor_telegram_user_id=100,
                               actor_role=Role.ADMINISTRATOR)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 2
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 1


async def test_concurrent_code_consumption(database):
    _, factory, _ = database
    *_, order_id = await seed(factory)
    async with factory.begin() as session:
        code_id = (await replace_code_record(session, order_id=order_id, code_hash="hash")).id

    async def consume():
        async with factory.begin() as session:
            return await consume_code_record(session, code_id=code_id, order_id=order_id)

    assert sorted(await asyncio.gather(consume(), consume())) == [False, True]


async def test_invalid_transition_does_not_write(database):
    _, factory, _ = database
    *_, order_id = await seed(factory)
    async with factory.begin() as session:
        with pytest.raises(InvalidTransition):
            await transition_order(session, order_id=order_id, expected_status=S.SCHEDULED,
                                   target_status=S.READY_FOR_PICKUP)
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == S.SCHEDULED
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 0


@pytest.mark.parametrize("values", [{"waiting_minutes": 0}, {"code_lifetime_minutes": 15}])
async def test_invalid_defaults_rejected(database, values):
    _, factory, _ = database
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            await session.execute(update(Defaults).values(**values))


async def test_order_waiting_snapshot_survives_default_changes_and_restart(database):
    engine, factory, _ = database
    _, courier, customer, _ = await seed(factory)
    schedule = scheduled_utc(date(2026, 9, 8), time(0, 15))

    async def create():
        async with factory.begin() as session:
            # Even a stale draft's waiting value is replaced at creation time.
            order = Order(name="Заказ", scheduled_at=schedule, pickup_address="Адрес",
                          courier_id=courier, customer_id=customer, waiting_minutes=999)
            event = await record_order(session, order, actor_telegram_user_id=100,
                                       actor_role=Role.ADMINISTRATOR)
            assert event.details["waiting_minutes"] == order.waiting_minutes
            return order.id

    first_id = await create()
    async with factory.begin() as session:
        await session.execute(update(Defaults).where(Defaults.id == 1).values(waiting_minutes=30))
    second_id = await create()
    await engine.dispose()
    async with factory() as session:
        first = await session.get(Order, first_id)
        second = await session.get(Order, second_id)
        assert first.waiting_minutes == 15
        assert second.waiting_minutes == 30
        assert first.waiting_deadline == schedule + timedelta(minutes=15)
        assert second.waiting_deadline == schedule + timedelta(minutes=30)
        assert first.scheduled_at == datetime(2026, 9, 7, 18, 15, tzinfo=timezone.utc)
        assert first.scheduled_date == date(2026, 9, 8)
        assert first.scheduled_time == time(0, 15)


@pytest.mark.parametrize("outcome", [S.COMPLETED, S.DISPUTED])
async def test_after_verification_no_show_and_cancellation_leave_order_unchanged(database, outcome):
    _, factory, _ = database
    *_, order_id = await seed(factory)
    path = [S.SCHEDULED, S.COURIER_ARRIVED, S.LOCATION_SUBMITTED, S.PHOTO_SUBMITTED,
            S.READY_FOR_PICKUP, S.AWAITING_CODE_CONFIRMATION, S.AWAITING_CUSTOMER_CONFIRMATION]
    async with factory.begin() as session:
        for previous, target in zip(path, path[1:]):
            await transition_order(session, order_id=order_id, expected_status=previous,
                                   target_status=target)
        count = await session.scalar(select(func.count()).select_from(OrderEvent))
        for target in (S.CUSTOMER_NO_SHOW, S.CANCELLED):
            with pytest.raises(InvalidTransition):
                await transition_order(session, order_id=order_id,
                                       expected_status=S.AWAITING_CUSTOMER_CONFIRMATION,
                                       target_status=target)
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == count
        order = await session.get(Order, order_id)
        assert order.status == S.AWAITING_CUSTOMER_CONFIRMATION
        assert order.no_show_at is None and order.cancelled_at is None
        await transition_order(session, order_id=order_id,
                               expected_status=S.AWAITING_CUSTOMER_CONFIRMATION,
                               target_status=outcome)
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == outcome


async def test_missing_waiting_defaults_does_not_create_order(database):
    _, factory, _ = database
    _, courier, customer, _ = await seed(factory)
    async with factory.begin() as session:
        await session.execute(delete(Defaults))
        with pytest.raises(ValueError, match="defaults"):
            await record_order(session, Order(name="Заказ", scheduled_at=utc_now(),
                               pickup_address="Адрес", courier_id=courier, customer_id=customer),
                               actor_telegram_user_id=100, actor_role=Role.ADMINISTRATOR)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 1
        assert await session.scalar(select(func.count()).select_from(OrderEvent)) == 0


@pytest.mark.parametrize("location", [
    {"location_type": "TELEGRAM", "latitude": 42.8746, "longitude": 74.5698, "location_url": None},
    {"location_type": "2GIS", "latitude": None, "longitude": None,
     "location_url": "https://2gis.kg/bishkek/geo/70030076127982829"},
])
async def test_all_record_types_and_evidence_fields_survive_reopening(database, location):
    """Verify stored values through a new engine, not the ORM identity cache."""
    engine, factory, config = database
    admin, courier, customer, order_id = await seed(factory)
    instant = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    user_values = {
        "telegram_user_id": 200, "username": "test_courier",
        "telegram_display_name": "Тестовый курьер", "display_name": "Алексей",
        "role": Role.COURIER, "active": True, "registered_at": instant,
    }
    invitation_values = {
        "token_hash": "test-invitation-verifier", "intended_role": Role.COURIER,
        "display_name": "Алексей", "created_at": instant,
        "used_at": instant + timedelta(seconds=1), "revoked": False,
        "invited_by_id": admin, "participant_id": courier,
    }
    default_values = {
        "pickup_address": "Бишкек, тестовый адрес", "courier_id": courier,
        "customer_id": customer, "waiting_minutes": 25, "code_lifetime_minutes": 20,
    }
    order_values = {
        "name": "Заказ №154", "product_description": "Ноутбук",
        "scheduled_at": instant, "pickup_address": "Бишкек, тестовый адрес",
        "courier_id": courier, "customer_id": customer, "waiting_minutes": 15,
        "created_at": instant - timedelta(hours=1),
        "arrived_at": instant + timedelta(minutes=1),
        **location, "location_submitted_at": instant + timedelta(minutes=2),
        "photo_file_id": "test-telegram-photo-file-id",
        "photo_submitted_at": instant + timedelta(minutes=3),
        "ready_at": instant + timedelta(minutes=4),
        "code_verified_at": instant + timedelta(minutes=5),
        "customer_confirmed_at": instant + timedelta(minutes=6),
        "completed_at": instant + timedelta(minutes=6),
        "cancelled_at": None, "no_show_at": None, "disputed_at": None,
    }
    code_values = {
        "order_id": order_id, "code_hash": "test-code-verifier",
        "generated_at": order_values["ready_at"],
        "expires_at": order_values["ready_at"] + timedelta(minutes=20),
        "used_at": order_values["code_verified_at"], "invalidated_at": None,
    }
    event_values = {
        "order_id": order_id, "event_type": "COMPLETED",
        "occurred_at": order_values["completed_at"],
        "actor_telegram_user_id": 300, "actor_role": Role.CUSTOMER,
        "details": {"previous_status": "AWAITING_CUSTOMER_CONFIRMATION",
                    "new_status": "COMPLETED", "location": location,
                    "photo_file_id": order_values["photo_file_id"],
                    "code_expires_at": code_values["expires_at"].isoformat()},
    }
    async with factory.begin() as session:
        await session.execute(update(User).where(User.id == courier).values(**user_values))
        await session.execute(update(Defaults).where(Defaults.id == 1).values(**default_values))
        await session.execute(update(Order).where(Order.id == order_id).values(**order_values))
        invitation = Invitation(**invitation_values)
        code = PickupCode(**code_values)
        event = OrderEvent(**event_values)
        session.add_all([invitation, code, event])
        await session.flush()
        records = [(User, courier, user_values), (Invitation, invitation.id, invitation_values),
                   (Defaults, 1, default_values), (Order, order_id, order_values),
                   (PickupCode, code.id, code_values), (OrderEvent, event.id, event_values)]
    await engine.dispose()
    reopened = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        async with session_factory(reopened)() as session:
            for model, identifier, expected in records:
                row = await session.get(model, identifier)
                assert row is not None
                assert {key: getattr(row, key) for key in expected} == expected
    finally:
        await reopened.dispose()
