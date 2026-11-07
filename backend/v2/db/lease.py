"""Single prototype scheduler lease with monotonically increasing fencing token."""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update

from .commands import Conflict
from .engine import unit_of_work
from .schema import leases, now


@dataclass(frozen=True)
class Lease:
    owner: str
    generation: int
    name: str = "prototype_scheduler"


def acquire(engine, owner: str, ttl_seconds: int = 30) -> Lease:
    if not owner or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
        raise ValueError("unique owner and TTL of 1..300 wall seconds required")
    with unit_of_work(engine) as tx:
        current_time = now()
        row = tx.execute(select(leases).where(leases.c.name == "prototype_scheduler")).mappings().first()
        if row and row["expires_at"] > current_time:
            raise Conflict("another scheduler lease is active")
        generation = row["generation"] + 1 if row else 1
        values = dict(owner=owner, generation=generation, expires_at=current_time + timedelta(seconds=ttl_seconds))
        if row:
            tx.execute(update(leases).where(leases.c.name == "prototype_scheduler").values(**values))
        else:
            tx.execute(leases.insert().values(name="prototype_scheduler", **values))
        return Lease(owner, generation)


def require_lease(tx, lease: Lease):
    row = tx.execute(select(leases.c.owner).where(leases.c.name == lease.name,
        leases.c.owner == lease.owner, leases.c.generation == lease.generation,
        leases.c.expires_at > now())).first()
    if row is None:
        raise Conflict("scheduler lease expired or superseded")


def renew(engine, lease: Lease, ttl_seconds: int = 30):
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
        raise ValueError("TTL must be 1..300 wall seconds")
    with unit_of_work(engine) as tx:
        require_lease(tx, lease)
        tx.execute(update(leases).where(leases.c.name == lease.name).values(
            expires_at=now() + timedelta(seconds=ttl_seconds)))


def release(engine, lease: Lease):
    with unit_of_work(engine) as tx:
        require_lease(tx, lease)
        tx.execute(update(leases).where(leases.c.name == lease.name).values(expires_at=now()))
