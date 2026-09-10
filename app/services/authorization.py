from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, User


class AccessDenied(PermissionError):
    """The Telegram identity has no active binding with a permitted role."""


async def require_role(session: AsyncSession, telegram_user_id: int, *roles: Role) -> User:
    user = await session.scalar(
        select(User).where(User.telegram_user_id == telegram_user_id, User.active.is_(True))
        .execution_options(populate_existing=True)
    )
    if user is None or user.role not in roles:
        raise AccessDenied("Active role required")
    return user
