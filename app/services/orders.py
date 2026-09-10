"""Atomic persistence of state changes; no Telegram workflow is implemented here."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import Defaults, Order, OrderEvent, OrderStatus, Role
from app.services.event_log import append_event
from app.services.state_machine import validate_transition


class StaleOrder(ValueError):
    """The order does not exist or another action already changed its state."""


MILESTONES = {
    OrderStatus.COURIER_ARRIVED: "arrived_at",
    OrderStatus.LOCATION_SUBMITTED: "location_submitted_at",
    OrderStatus.PHOTO_SUBMITTED: "photo_submitted_at",
    OrderStatus.READY_FOR_PICKUP: "ready_at",
    OrderStatus.AWAITING_CUSTOMER_CONFIRMATION: "code_verified_at",
    OrderStatus.COMPLETED: "completed_at",
    OrderStatus.CANCELLED: "cancelled_at",
    OrderStatus.CUSTOMER_NO_SHOW: "no_show_at",
    OrderStatus.DISPUTED: "disputed_at",
}


async def record_order(
    session: AsyncSession, order: Order, *, actor_telegram_user_id: int,
    actor_role: Role,
) -> OrderEvent:
    """Persist an already validated draft and creation event atomically.

    Field collection/participant selection/role checks belong to task 5. The draft
    must be transient (not yet added to the session).
    """
    from sqlalchemy import inspect

    if not inspect(order).transient or order._status not in (None, OrderStatus.SCHEDULED):
        raise ValueError("A new scheduled order is required")
    now = utc_now()
    async with session.begin_nested():
        waiting_minutes = await session.scalar(select(Defaults.waiting_minutes).where(Defaults.id == 1))
        if waiting_minutes is None:
            raise ValueError("Operational defaults are missing; apply migrations first")
        order.waiting_minutes = waiting_minutes
        order._status = OrderStatus.SCHEDULED
        order.created_at = now
        session.add(order)
        await session.flush()
        event = await append_event(
            session, order_id=order.id, event_type="ORDER_CREATED",
            actor_telegram_user_id=actor_telegram_user_id, actor_role=actor_role,
            details={"new_status": OrderStatus.SCHEDULED.value,
                     "waiting_minutes": waiting_minutes}, occurred_at=now,
        )
    return event


async def transition_order(
    session: AsyncSession, *, order_id: int, expected_status: OrderStatus,
    target_status: OrderStatus, actor_telegram_user_id: int | None = None,
    actor_role: Role | None = None, metadata: dict | None = None,
) -> OrderEvent:
    """Compare-and-set plus audit in one savepoint within the caller's transaction.

    This internal foundation validates structural state edges. Future action
    services enforce active role/assignment, evidence, codes, and timing first.
    A failed event insert rolls back the change even if the caller catches it.
    """
    validate_transition(expected_status, target_status)
    now = utc_now()
    values = {"_status": target_status}
    if milestone := MILESTONES.get(target_status):
        values[milestone] = now
    if target_status == OrderStatus.COMPLETED:
        values["customer_confirmed_at"] = now
    details = dict(metadata or {})
    details.update(previous_status=expected_status.value, new_status=target_status.value)
    async with session.begin_nested():
        result = await session.execute(
            update(Order).where(Order.id == order_id, Order._status == expected_status)
            .values(**values).execution_options(synchronize_session="fetch")
        )
        if result.rowcount != 1:
            raise StaleOrder("Order is missing or its state has changed")
        event = await append_event(
            session, order_id=order_id, event_type=target_status.value,
            actor_telegram_user_id=actor_telegram_user_id, actor_role=actor_role,
            details=details, occurred_at=now,
        )
    return event
