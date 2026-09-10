"""Secure, single-use courier/customer registration invitations."""

import hashlib
import re
import secrets

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import Invitation, Role, User
from app.services.authorization import require_role

PARTICIPANT_ROLES = (Role.COURIER, Role.CUSTOMER)


class InvalidInvitation(ValueError):
    pass


class RoleConflict(ValueError):
    pass


def validate_name(name: str) -> str:
    name = name.strip()
    if not 1 <= len(name) <= 100 or any(ord(character) < 32 for character in name):
        raise ValueError("Participant name must contain 1–100 printable characters")
    return name


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


async def create_invitation(
    session: AsyncSession, *, administrator_id: int, role: Role, name: str,
) -> str:
    administrator = await require_role(session, administrator_id, Role.ADMINISTRATOR)
    if role not in PARTICIPANT_ROLES:
        raise ValueError("Participant role required")
    name = validate_name(name)
    token = secrets.token_urlsafe(32)
    session.add(Invitation(token_hash=token_hash(token), intended_role=role,
                           display_name=name, invited_by_id=administrator.id))
    await session.flush()
    return token


async def redeem_invitation(
    session: AsyncSession, *, token: str, telegram_user_id: int,
    username: str | None = None, display_name: str | None = None,
) -> User:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise InvalidInvitation("Invalid invitation")
    async with session.begin_nested():
        # The first database operation is a conditional write. Competing claims
        # serialize before reading or inserting a binding (including two invites
        # being claimed by the same Telegram account).
        result = await session.execute(
            update(Invitation).where(
                Invitation.token_hash == token_hash(token),
                Invitation.used_at.is_(None), Invitation.revoked.is_(False),
                ~exists().where(User.telegram_user_id == telegram_user_id, User.active.is_(True)),
            ).values(used_at=utc_now()).returning(
                Invitation.id, Invitation.intended_role, Invitation.display_name,
            )
        )
        invitation = result.first()
        if invitation is None:
            if await session.scalar(select(exists().where(
                User.telegram_user_id == telegram_user_id, User.active.is_(True)
            ))):
                raise RoleConflict("An active role is already bound")
            raise InvalidInvitation("Invalid or unavailable invitation")
        user = User(telegram_user_id=telegram_user_id, username=username,
                    telegram_display_name=display_name, display_name=invitation.display_name,
                    role=invitation.intended_role, active=True, registered_at=utc_now())
        session.add(user)
        await session.flush()
        await session.execute(update(Invitation).where(Invitation.id == invitation.id)
                              .values(participant_id=user.id))
    return user
