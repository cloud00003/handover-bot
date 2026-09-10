"""Assigned courier arrival and evidence recording; readiness is Task 8."""

import math
import re
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select, update

from app.db.base import utc_now
from app.db.models import Order, OrderStatus as S, Role, User
from app.services.authorization import AccessDenied, require_role
from app.services.orders import StaleOrder, transition_order
from app.services.state_machine import TRANSITIONS
from app.time import OPERATIONAL_TIMEZONE, local_datetime

ACTIVE = tuple(status for status, targets in TRANSITIONS.items() if targets)
PAGE_SIZE = 3


class ArrivalTooEarly(ValueError):
    pass


class InvalidEvidence(ValueError):
    pass


def arrival_opens(order):
    local_date = local_datetime(order.scheduled_at).date()
    midnight = datetime.combine(local_date, time.min, OPERATIONAL_TIMEZONE).astimezone(timezone.utc)
    return max(midnight, order.scheduled_at - timedelta(minutes=30))


async def assigned_order(session, telegram_id, order_id, *, writing=False):
    if writing:
        # Serialize SQLite mutations before reading status, as with cancellation.
        # The no-op write is itself restricted to the active assigned courier.
        identity = select(User.id).where(User.telegram_user_id == telegram_id,
                                         User.active.is_(True), User.role == Role.COURIER)
        await session.execute(update(Order).where(Order.id == order_id,
            Order.courier_id.in_(identity)).values(id=Order.id))
    courier = await require_role(session, telegram_id, Role.COURIER)
    order = await session.get(Order, order_id, populate_existing=True)
    if order is None or order.courier_id != courier.id:
        raise AccessDenied("Assigned courier required")
    if order.status not in ACTIVE:
        raise StaleOrder("Order is terminal")
    return order


async def list_assigned(session, telegram_id, page=0):
    courier = await require_role(session, telegram_id, Role.COURIER)
    return list((await session.scalars(select(Order).where(Order.courier_id == courier.id,
        Order._status.in_(ACTIVE)).order_by(Order.scheduled_at, Order.id)
        .offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1))).all())


async def arrive(session, telegram_id, order_id):
    order = await assigned_order(session, telegram_id, order_id, writing=True)
    if order.status != S.SCHEDULED:
        raise StaleOrder("Arrival already recorded")
    if utc_now() < arrival_opens(order):
        raise ArrivalTooEarly
    await transition_order(session, order_id=order.id, expected_status=S.SCHEDULED,
                           target_status=S.COURIER_ARRIVED,
                           actor_telegram_user_id=telegram_id, actor_role=Role.COURIER)
    return order


def coordinates(latitude, longitude):
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (latitude, longitude)):
        raise InvalidEvidence
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise InvalidEvidence
    return dict(location_type="TELEGRAM", latitude=latitude, longitude=longitude, location_url=None)


def location_link(text):
    if not isinstance(text, str):
        raise InvalidEvidence
    # Never resolve links or call map APIs. Preserve the submitted URL exactly.
    for raw in re.findall(r"https?://[^\s<>\"']+", text):
        try:
            url = urlsplit(raw)
            host = url.hostname or ""
            if (host in {"2gis.ru", "2gis.kg", "2gis.kz", "2gis.com", "go.2gis.com",
                         "www.2gis.ru", "www.2gis.kg", "www.2gis.kz", "www.2gis.com"}
                    and url.username is None and url.password is None and url.port is None
                    and url.path.strip("/") and len(raw) <= 2000):
                return dict(location_type="2GIS", latitude=None, longitude=None, location_url=raw)
        except ValueError:
            pass
    raise InvalidEvidence


async def submit_location(session, telegram_id, order_id, *, latitude=None, longitude=None, text=None):
    order = await assigned_order(session, telegram_id, order_id, writing=True)
    if order.status != S.COURIER_ARRIVED or order.arrived_at is None:
        raise StaleOrder("Arrival required; location may only be recorded once")
    evidence = location_link(text) if text is not None else coordinates(latitude, longitude)
    async with session.begin_nested():
        for field, value in evidence.items():
            setattr(order, field, value)
        await session.flush()
        await transition_order(session, order_id=order.id, expected_status=S.COURIER_ARRIVED,
                               target_status=S.LOCATION_SUBMITTED, actor_telegram_user_id=telegram_id,
                               actor_role=Role.COURIER, metadata={k: v for k, v in evidence.items() if v is not None})
    return order


async def submit_photo(session, telegram_id, order_id, file_id):
    order = await assigned_order(session, telegram_id, order_id, writing=True)
    if order.status != S.LOCATION_SUBMITTED or order.location_submitted_at is None:
        raise StaleOrder("Location required; photo may only be recorded once")
    if not isinstance(file_id, str) or not file_id.strip():
        raise InvalidEvidence
    async with session.begin_nested():
        order.photo_file_id = file_id
        await session.flush()
        await transition_order(session, order_id=order.id, expected_status=S.LOCATION_SUBMITTED,
                               target_status=S.PHOTO_SUBMITTED, actor_telegram_user_id=telegram_id,
                               actor_role=Role.COURIER, metadata={"photo_file_id": file_id})
    return order
