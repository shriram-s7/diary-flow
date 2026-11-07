"""Atomic command + effects + audit + outbox. Call only after authorization.

Handlers receive this transaction's connection and must never commit or do I/O.
SQLAlchemy Core is the repository layer: no ORM session is shared across callers.
"""
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select, update

from v2.domain.types import CommandContext
from .engine import unit_of_work
from .schema import audit, commands, events, outbox


class Conflict(ValueError):
    """409 equivalent at the future transport boundary."""


def canonical(value):
    def validate(item):
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise ValueError("JSON keys must be strings")
            for part in item.values():
                validate(part)
        elif isinstance(item, list):
            for part in item:
                validate(part)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("payload must be JSON data; encode Decimal as strings")
    validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class Effects:
    def __init__(self, connection, context):
        self.connection = connection
        self.context = context

    def notify(self, topic, recipient, payload):
        canonical(payload)
        self.connection.execute(outbox.insert().values(
            run_id=self.context.run_id, id=str(uuid4()), command_id=self.context.command_id,
            topic=topic, recipient=recipient, payload=payload))


def execute(engine, context: CommandContext, kind: str, payload: dict, handler, *,
            sim_time, event_key=None, lease=None, authorize=None):
    """Return committed result on exact retries, even if aggregate revision advanced.

    event_key=(event_type, scope) uses context.delivery_date. Different event
    invocation IDs share one durable event effect if the event payload is equal.
    Lease fencing is required for scheduled events, not human business commands.
    """
    if not isinstance(context, CommandContext) or not kind or not isinstance(payload, dict):
        raise ValueError("validated context, nonempty kind and object payload required")
    request_hash = digest({"context": context.model_dump(mode="json"), "kind": kind, "payload": payload})
    with unit_of_work(engine) as tx:
        # Auth must run before an idempotent replay, in this same transaction.
        if authorize is not None:
            authorize(tx)
        if event_key is not None:
            from .lease import require_lease
            if lease is None:
                raise ValueError("scheduled event requires scheduler lease")
            require_lease(tx, lease)
        old = tx.execute(select(commands).where(
            commands.c.run_id == context.run_id, commands.c.id == context.command_id)).mappings().first()
        if old:
            if not old["completed"]:
                raise Conflict("incomplete command requires recovery; cannot replay")
            if old["payload_hash"] != request_hash:
                raise Conflict("idempotency key reused with different actor, context or payload")
            return old["result"]
        if event_key is not None:
            event_type, scope = event_key
            old_event = tx.execute(select(events).where(events.c.run_id == context.run_id,
                events.c.delivery_date == context.delivery_date, events.c.event_type == event_type,
                events.c.scope == scope)).mappings().first()
            if old_event:
                if old_event["input_hash"] != digest({"kind": kind, "payload": payload}):
                    raise Conflict("completed event cannot be rerun with different inputs")
                return old_event["result"]
        tx.execute(commands.insert().values(run_id=context.run_id, id=context.command_id,
            actor_id=context.actor_id, kind=kind, payload_hash=request_hash, result={}, sim_time=sim_time))
        result = handler(Effects(tx, context))
        if not isinstance(result, dict):
            raise ValueError("command result must be a JSON object")
        result = json.loads(canonical(result))
        tx.execute(update(commands).where(commands.c.run_id == context.run_id,
            commands.c.id == context.command_id).values(result=result, completed=True))
        tx.execute(audit.insert().values(run_id=context.run_id, id=str(uuid4()), command_id=context.command_id,
            actor_id=context.actor_id, action=kind, reason=context.reason,
            details={"payload_hash": request_hash, "expected_revision": context.expected_revision}))
        if event_key is not None:
            tx.execute(events.insert().values(run_id=context.run_id, id=str(uuid4()),
                delivery_date=context.delivery_date, event_type=event_key[0], scope=event_key[1],
                command_id=context.command_id, input_hash=digest({"kind": kind, "payload": payload}), result=result))
        return result


def compare_and_swap(tx, table, run_id, row_id, expected_revision, **changes):
    """Reject stale edits. Call from a command handler to obtain audit/deduplication."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected revision must be a nonnegative integer")
    if not changes or set(changes) & {"id", "run_id", "revision", "created_at"}:
        raise ValueError("cannot edit identity/version fields")
    result = tx.execute(update(table).where(table.c.run_id == run_id, table.c.id == row_id,
        table.c.revision == expected_revision).values(**changes, revision=expected_revision + 1))
    if result.rowcount != 1:
        raise Conflict("aggregate missing or revision changed")
    return expected_revision + 1
