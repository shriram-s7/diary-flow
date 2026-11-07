"""Allowed lifecycle edges only. Services must also enforce actor/time/stock guards."""
from types import MappingProxyType


class InvalidTransition(ValueError):
    pass


TRANSITIONS = MappingProxyType({
    "order": MappingProxyType({
        "draft": frozenset({"confirmed"}),
        "confirmed": frozenset({"locked"}),
        "locked": frozenset({"allocated"}),
        "allocated": frozenset({"dispatched"}),
        "dispatched": frozenset({"delivered", "exception"}),
        "delivered": frozenset({"closed"}),
        "exception": frozenset({"delivered", "closed"}),
        "closed": frozenset(),
    }),
    "proposal": MappingProxyType({
        "pending": frozenset({"approved", "rejected", "expired", "superseded"}),
        "approved": frozenset(), "rejected": frozenset(),
        "expired": frozenset(), "superseded": frozenset(),
    }),
    "trip": MappingProxyType({
        "planned": frozenset({"loading", "cancelled"}),
        "loading": frozenset({"dispatched", "cancelled"}),
        "dispatched": frozenset({"blocked", "returning"}),
        "blocked": frozenset({"dispatched", "returning"}),
        "returning": frozenset({"blocked", "completed"}),
        "completed": frozenset(), "cancelled": frozenset(),
    }),
    "receipt": MappingProxyType({
        "pending": frozenset({"unconfirmed"}),
        "unconfirmed": frozenset({"confirmed", "disputed"}),
        "disputed": frozenset({"confirmed"}),
        "confirmed": frozenset(),
    }),
    "event": MappingProxyType({
        "pending": frozenset({"running", "waiting"}),
        "waiting": frozenset({"running"}),
        "running": frozenset({"completed", "failed", "waiting"}),
        "failed": frozenset({"running"}),
        "completed": frozenset(),
    }),
    "day": MappingProxyType({
        "open": frozenset({"closing"}),
        "closing": frozenset({"open", "closed", "closed_with_exceptions"}),
        "closed": frozenset(), "closed_with_exceptions": frozenset(),
    }),
})


def transition(kind: str, current: str, target: str) -> str:
    if kind not in TRANSITIONS or current not in TRANSITIONS[kind]:
        raise InvalidTransition(f"unknown lifecycle/state: {kind}/{current}")
    if target not in TRANSITIONS[kind][current]:
        raise InvalidTransition(f"invalid {kind} transition: {current} -> {target}")
    return target
