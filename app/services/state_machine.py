"""Structural transitions only; handlers must use the transactional service.

Action-specific authorization, evidence validation, and timing gates are added
with their respective workflows. Verified handovers can only complete or dispute.
"""

from types import MappingProxyType

from app.db.models import OrderStatus as S


class InvalidTransition(ValueError):
    pass


TRANSITIONS = MappingProxyType({
    S.SCHEDULED: frozenset({S.COURIER_ARRIVED, S.CANCELLED, S.CUSTOMER_NO_SHOW}),
    S.COURIER_ARRIVED: frozenset({S.LOCATION_SUBMITTED, S.CANCELLED, S.CUSTOMER_NO_SHOW}),
    S.LOCATION_SUBMITTED: frozenset({S.PHOTO_SUBMITTED, S.CANCELLED, S.CUSTOMER_NO_SHOW}),
    S.PHOTO_SUBMITTED: frozenset({S.READY_FOR_PICKUP, S.CANCELLED, S.CUSTOMER_NO_SHOW}),
    S.READY_FOR_PICKUP: frozenset({S.AWAITING_CODE_CONFIRMATION, S.CANCELLED, S.CUSTOMER_NO_SHOW}),
    S.AWAITING_CODE_CONFIRMATION: frozenset({S.AWAITING_CUSTOMER_CONFIRMATION, S.CANCELLED, S.CUSTOMER_NO_SHOW}),
    S.AWAITING_CUSTOMER_CONFIRMATION: frozenset({S.COMPLETED, S.DISPUTED}),
    S.COMPLETED: frozenset(),
    S.CUSTOMER_NO_SHOW: frozenset(),
    S.CANCELLED: frozenset(),
    S.DISPUTED: frozenset(),
})


def validate_transition(previous: S, target: S) -> None:
    if target not in TRANSITIONS.get(previous, ()):
        raise InvalidTransition(f"Transition from {previous} to {target} is not allowed")
