"""Persisted identities, order evidence, defaults, and audit history."""

from datetime import date, datetime, time, timedelta
from enum import StrEnum

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Enum, ForeignKey, Index, Integer,
    JSON, String, Text, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime, utc_now
from app.time import local_datetime


class Role(StrEnum):
    ADMINISTRATOR = "ADMINISTRATOR"
    COURIER = "COURIER"
    CUSTOMER = "CUSTOMER"


class OrderStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    COURIER_ARRIVED = "COURIER_ARRIVED"
    LOCATION_SUBMITTED = "LOCATION_SUBMITTED"
    PHOTO_SUBMITTED = "PHOTO_SUBMITTED"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    AWAITING_CODE_CONFIRMATION = "AWAITING_CODE_CONFIRMATION"
    AWAITING_CUSTOMER_CONFIRMATION = "AWAITING_CUSTOMER_CONFIRMATION"
    COMPLETED = "COMPLETED"
    CUSTOMER_NO_SHOW = "CUSTOMER_NO_SHOW"
    CANCELLED = "CANCELLED"
    DISPUTED = "DISPUTED"


def enum_type(enum, name):
    return Enum(enum, name=name, native_enum=False, create_constraint=True,
                validate_strings=True)


class User(Base):
    """One registration/binding per row; unbinding deactivates, never deletes it."""

    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_active_telegram", "telegram_user_id", unique=True,
              sqlite_where=text("active = 1")),
        Index("uq_users_active_administrator", "role", unique=True,
              sqlite_where=text("active = 1 AND role = 'ADMINISTRATOR'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(255))
    telegram_display_name: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[Role] = mapped_column(enum_type(Role, "user_role"))
    active: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="active_bool"), default=True, server_default=text("1"))
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP"))


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("intended_role IN ('COURIER', 'CUSTOMER')", name="participant_role"),
        CheckConstraint("length(trim(display_name)) > 0", name="name_required"),
        CheckConstraint("length(token_hash) > 0", name="hash_required"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True)
    intended_role: Mapped[Role] = mapped_column(enum_type(Role, "invitation_role"))
    display_name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP"))
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="revoked_bool"), default=False, server_default=text("0"))
    invited_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    participant_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class Defaults(Base):
    __tablename__ = "defaults"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("waiting_minutes > 0", name="positive_wait"),
        CheckConstraint("code_lifetime_minutes = 20", name="code_lifetime"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    pickup_address: Mapped[str | None] = mapped_column(Text)
    courier_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    waiting_minutes: Mapped[int] = mapped_column(default=15, server_default=text("15"))
    code_lifetime_minutes: Mapped[int] = mapped_column(default=20, server_default=text("20"))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_required"),
        CheckConstraint("length(trim(pickup_address)) > 0", name="address_required"),
        CheckConstraint("courier_id != customer_id", name="distinct_participants"),
        CheckConstraint("location_type IS NULL OR location_type IN ('TELEGRAM', '2GIS')", name="location_type"),
        CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint("waiting_minutes > 0", name="positive_wait"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    product_description: Mapped[str | None] = mapped_column(Text)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # Required snapshot; record_order reads the current default at creation.
    waiting_minutes: Mapped[int] = mapped_column(Integer)
    pickup_address: Mapped[str] = mapped_column(Text)
    courier_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    _status: Mapped[OrderStatus] = mapped_column("status", enum_type(OrderStatus, "order_status"), default=OrderStatus.SCHEDULED, server_default="SCHEDULED")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP"))
    arrived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    location_type: Mapped[str | None] = mapped_column(String(16))
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    location_url: Mapped[str | None] = mapped_column(Text)
    location_submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    photo_file_id: Mapped[str | None] = mapped_column(Text)
    photo_submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ready_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    code_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    customer_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    no_show_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    disputed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    @property
    def status(self) -> OrderStatus:
        """Status changes go through the transition service."""
        return self._status

    @property
    def scheduled_date(self) -> date:
        return local_datetime(self.scheduled_at).date()

    @property
    def scheduled_time(self) -> time:
        return local_datetime(self.scheduled_at).time()

    @property
    def waiting_deadline(self) -> datetime:
        """Initial waiting deadline; extensions belong to the later workflow."""
        return self.scheduled_at + timedelta(minutes=self.waiting_minutes)


class PickupCode(Base):
    __tablename__ = "pickup_codes"
    __table_args__ = (
        Index("uq_pickup_codes_active_order", "order_id", unique=True,
              sqlite_where=text("used_at IS NULL AND invalidated_at IS NULL")),
        CheckConstraint("length(code_hash) > 0", name="hash_required"),
        CheckConstraint("expires_at > generated_at", name="positive_lifetime"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    code_hash: Mapped[str] = mapped_column(String(255))
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP"))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    invalidated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OrderEvent(Base):
    __tablename__ = "order_events"
    __table_args__ = (Index("ix_order_events_timeline", "order_id", "occurred_at", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    event_type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP"))
    actor_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    actor_role: Mapped[Role | None] = mapped_column(enum_type(Role, "event_actor_role"))
    details: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
