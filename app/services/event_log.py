from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import OrderEvent, Role


async def append_event(
    session: AsyncSession, *, order_id: int, event_type: str,
    actor_telegram_user_id: int | None = None, actor_role: Role | None = None,
    details: dict | None = None, occurred_at: datetime | None = None,
) -> OrderEvent:
    """Append inside the caller's transaction; never commits independently.

    occurred_at is an internal server timestamp shared with a state change.
    Callers must not pass Telegram/device timestamps or secrets in metadata.
    """
    event = OrderEvent(
        order_id=order_id, event_type=event_type,
        actor_telegram_user_id=actor_telegram_user_id, actor_role=actor_role,
        details=dict(details or {}), occurred_at=occurred_at or utc_now(),
    )
    session.add(event)
    await session.flush()
    return event
