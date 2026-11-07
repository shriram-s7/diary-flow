"""Minimal atomic stock-reservation primitive; full lot/FEFO policy belongs to P05."""
from uuid import uuid4

from sqlalchemy import update

from .commands import Conflict
from .schema import balances, reservations


def reserve(effects, balance_id, units):
    if type(units) is not int or units <= 0:
        raise ValueError("reservation units must be a positive integer")
    tx, context = effects.connection, effects.context
    result = tx.execute(update(balances).where(
        balances.c.run_id == context.run_id, balances.c.id == balance_id,
        balances.c.revision == context.expected_revision,
        balances.c.units - balances.c.reserved_units >= units,
    ).values(reserved_units=balances.c.reserved_units + units, revision=balances.c.revision + 1))
    if result.rowcount != 1:
        raise Conflict("stock missing, stale or insufficient")
    reservation_id = str(uuid4())
    tx.execute(reservations.insert().values(run_id=context.run_id, id=reservation_id,
        balance_id=balance_id, command_id=context.command_id, units=units, state="active"))
    return {"reservation_id": reservation_id, "units": units, "revision": context.expected_revision + 1}
