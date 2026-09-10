"""Validation and persistence for operational defaults and new orders."""

import re
from datetime import datetime

from sqlalchemy import select

from app.db.models import Defaults, Order, Role, User
from app.services.authorization import require_role
from app.services.orders import record_order
from app.time import scheduled_utc


class InvalidDraft(ValueError):
    pass


PARTICIPANT_FIELDS = {"courier_id": Role.COURIER, "customer_id": Role.CUSTOMER}
ORDER_FIELDS = ("name", "date", "time", "pickup_address", "courier_id", "customer_id", "product_description")
DEFAULT_FIELDS = ("pickup_address", "courier_id", "customer_id", "waiting_minutes")


def validate_field(field, value):
    if field == "waiting_minutes" and type(value) is int:
        value = str(value)
    if field in PARTICIPANT_FIELDS:
        if type(value) is not int or value <= 0:
            raise InvalidDraft(field)
        return value
    if not isinstance(value, str):
        raise InvalidDraft(field)
    value = value.strip()
    if field in ("date", "time"):
        pattern, fmt = ((r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", "%d.%m.%Y")
                        if field == "date" else (r"[0-9]{2}:[0-9]{2}", "%H:%M"))
        try:
            if not re.fullmatch(pattern, value):
                raise ValueError
            datetime.strptime(value, fmt)
        except ValueError:
            raise InvalidDraft(field) from None
    elif field == "waiting_minutes":
        if not re.fullmatch(r"[0-9]{1,10}", value) or not 1 <= int(value) <= 2147483647:
            raise InvalidDraft(field)
        return int(value)
    else:
        limit = {"name": 200, "pickup_address": 500, "product_description": 1000}.get(field)
        if limit is None or not value or len(value) > limit or any(
            not c.isprintable() and not (field == "product_description" and c == "\n") for c in value
        ):
            raise InvalidDraft(field)
    return value


async def participant(session, field, identity):
    identity = validate_field(field, identity)
    user = await session.scalar(select(User).where(
        User.id == identity, User.active.is_(True), User.role == PARTICIPANT_FIELDS[field],
    ).execution_options(populate_existing=True))
    if user is None:
        raise InvalidDraft(field)
    return user


async def read_defaults(session):
    defaults = await session.get(Defaults, 1)
    values = {field: getattr(defaults, field) for field in DEFAULT_FIELDS}
    for field in PARTICIPANT_FIELDS:
        try:
            await participant(session, field, values[field])
        except InvalidDraft:
            values[field] = None
    return values


async def save_default(session, administrator_id, field, value):
    await require_role(session, administrator_id, Role.ADMINISTRATOR)
    if field not in DEFAULT_FIELDS:
        raise InvalidDraft(field)
    value = validate_field(field, value)
    if field in PARTICIPANT_FIELDS:
        await participant(session, field, value)
    defaults = await session.get(Defaults, 1)
    setattr(defaults, field, value)
    await session.flush()


async def validate_draft(session, draft):
    values = {}
    for field in ORDER_FIELDS:
        value = draft.get(field)
        values[field] = (None if field == "product_description" and value is None
                         else validate_field(field, value))
    for field in PARTICIPANT_FIELDS:
        await participant(session, field, values[field])
    return values


async def create_order(session, administrator_id, draft):
    await require_role(session, administrator_id, Role.ADMINISTRATOR)
    values = await validate_draft(session, draft)
    scheduled_at = scheduled_utc(datetime.strptime(values.pop("date"), "%d.%m.%Y").date(),
                                 datetime.strptime(values.pop("time"), "%H:%M").time())
    order = Order(**values, scheduled_at=scheduled_at)
    await record_order(session, order, actor_telegram_user_id=administrator_id, actor_role=Role.ADMINISTRATOR)
    return order
