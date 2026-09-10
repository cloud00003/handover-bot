"""Conditional persistence operations for future invitation/code workflows.

No token generation, Telegram registration, or user-facing code validation here.
All operations participate in the caller's transaction and never commit it.
"""

from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import Invitation, PickupCode

CODE_LIFETIME = timedelta(minutes=20)


async def consume_invitation(session: AsyncSession, invitation_id: int) -> bool:
    result = await session.execute(
        update(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.used_at.is_(None), Invitation.revoked.is_(False),
        ).values(used_at=utc_now())
    )
    return result.rowcount == 1


async def replace_code_record(
    session: AsyncSession, *, order_id: int, code_hash: str,
) -> PickupCode:
    """Persist only a verifier; invalidate the previous code even if expired."""
    now = utc_now()
    async with session.begin_nested():
        await session.execute(
            update(PickupCode).where(
                PickupCode.order_id == order_id, PickupCode.used_at.is_(None),
                PickupCode.invalidated_at.is_(None),
            ).values(invalidated_at=now)
        )
        code = PickupCode(order_id=order_id, code_hash=code_hash,
                          generated_at=now, expires_at=now + CODE_LIFETIME)
        session.add(code)
        await session.flush()
    return code


async def consume_code_record(
    session: AsyncSession, *, code_id: int, order_id: int,
) -> bool:
    """Call only after secure verification in the later pickup-code service."""
    now = utc_now()
    result = await session.execute(
        update(PickupCode).where(
            PickupCode.id == code_id, PickupCode.order_id == order_id,
            PickupCode.used_at.is_(None), PickupCode.invalidated_at.is_(None),
            PickupCode.expires_at > now,
        ).values(used_at=now)
    )
    return result.rowcount == 1
