"""First-run administrator registration, guarded atomically in SQLite."""

from sqlalchemy import exists, literal, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import Role, User


async def is_initialized(session: AsyncSession) -> bool:
    # An inactive administrator must not reopen public administrator registration.
    return bool(await session.scalar(select(exists().where(User.role == Role.ADMINISTRATOR))))


async def register_first_administrator(
    session: AsyncSession, *, telegram_user_id: int,
    username: str | None = None, display_name: str | None = None,
) -> bool:
    """One conditional INSERT; caller owns the transaction and commit.

    Persisting the administrator row marks initialization. Unlike a read followed
    by an unconditional insert, concurrent confirmations cannot both succeed.
    """
    candidate = select(
        literal(telegram_user_id), literal(username), literal(display_name),
        literal(Role.ADMINISTRATOR.value), literal(True), literal(utc_now()),
    ).where(
        ~exists().where(User.role == Role.ADMINISTRATOR),
        ~exists().where(User.telegram_user_id == telegram_user_id, User.active.is_(True)),
    )
    result = await session.execute(
        insert(User).from_select(
            ["telegram_user_id", "username", "telegram_display_name", "role", "active", "registered_at"],
            candidate, include_defaults=False,
        ).on_conflict_do_nothing()
    )
    return result.rowcount == 1
