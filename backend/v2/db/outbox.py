"""At-least-once publication; consumers deduplicate by immutable outbox event ID."""
from sqlalchemy import select, update

from .engine import unit_of_work
from .schema import outbox, now


def pending(engine, limit=100):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be 1..1000")
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(outbox).where(
            outbox.c.published_at.is_(None)).order_by(outbox.c.created_at, outbox.c.id).limit(limit)).mappings()]


def publish_pending(engine, publish, limit=100):
    delivered = 0
    for event in pending(engine, limit):
        # No open database transaction during external publication.
        publish(event)
        with unit_of_work(engine) as tx:
            tx.execute(update(outbox).where(outbox.c.run_id == event["run_id"],
                outbox.c.id == event["id"], outbox.c.published_at.is_(None)).values(published_at=now()))
        delivered += 1
    return delivered
