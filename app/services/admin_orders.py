"""Administrator order reading and atomic cancellation."""

from sqlalchemy import exists, select, update

from app.db.models import Order, OrderEvent, OrderStatus as S, PickupCode, Role, User
from app.services.authorization import require_role
from app.services.orders import StaleOrder, transition_order
from app.services.state_machine import TRANSITIONS

TERMINAL = tuple(status for status, targets in TRANSITIONS.items() if not targets)
PAGE_SIZE = 3


def can_cancel(order):
    return order.code_verified_at is None and S.CANCELLED in TRANSITIONS[order.status]


async def get_order(session, order_id):
    order = await session.get(Order, order_id, populate_existing=True)
    if order is None:
        raise StaleOrder("Order missing")
    return order


async def list_orders(session, history=False, page=0):
    condition = Order._status.in_(TERMINAL) if history else Order._status.not_in(TERMINAL)
    return list((await session.scalars(select(Order).where(condition)
        .order_by(Order.scheduled_at.desc() if history else Order.scheduled_at, Order.id)
        .offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1))).all())


async def participants(session, order):
    return await session.get(User, order.courier_id), await session.get(User, order.customer_id)


async def code_expiry(session, order_id):
    # Select only the safe display field; do not load the secret verifier.
    return await session.scalar(select(PickupCode.expires_at).where(PickupCode.order_id == order_id)
                                .order_by(PickupCode.generated_at.desc(), PickupCode.id.desc()).limit(1))


async def timeline(session, order_id, page=0):
    return list((await session.scalars(select(OrderEvent).where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.occurred_at, OrderEvent.id).offset(page * 5).limit(6))).all())


async def cancel_order(session, administrator_id, order_id):
    # Acquire SQLite's write lock before reading status to serialize competing
    # confirmations. The no-op write is restricted to an active administrator.
    # Call in a fresh transaction; all changes below remain caller-owned.
    await session.execute(update(Order).where(Order.id == order_id, exists().where(
        User.telegram_user_id == administrator_id, User.active.is_(True), User.role == Role.ADMINISTRATOR,
    )).values(id=Order.id))
    await require_role(session, administrator_id, Role.ADMINISTRATOR)
    order = await get_order(session, order_id)
    if not can_cancel(order):
        raise StaleOrder("Cancellation no longer permitted")
    async with session.begin_nested():
        await transition_order(session, order_id=order_id, expected_status=order.status,
                               target_status=S.CANCELLED, actor_telegram_user_id=administrator_id,
                               actor_role=Role.ADMINISTRATOR)
        await session.execute(update(PickupCode).where(
            PickupCode.order_id == order_id, PickupCode.used_at.is_(None),
            PickupCode.invalidated_at.is_(None),
        ).values(invalidated_at=order.cancelled_at))
    courier, customer = await participants(session, order)
    return order, (courier.telegram_user_id, customer.telegram_user_id)
