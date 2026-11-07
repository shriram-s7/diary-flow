"""Pure arithmetic; quantities are computed by the backend, never by its clients."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from math import isqrt
from typing import Iterable

from .settings import DEFAULTS, Settings
from .types import PackedQuantity, Packaging, require_aware


def count(value: int, name: str = "units") -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def amount(value, name: str = "amount", *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return number


def pack(units: int, product: Packaging) -> PackedQuantity:
    units = count(units)
    slots = (units + product.units_per_container - 1) // product.units_per_container
    quantity = Decimal(units) * product.pack_size / 1000
    return PackedQuantity(
        units=units, slots=slots, planning_g=slots * product.full_container_g,
        returnable_containers=slots if product.returnable else 0,
        litres=quantity if product.dimension == "ml" else None,
        product_kg=quantity if product.dimension == "g" else None,
        measured_gross_g=(units * product.unit_gross_g + slots * product.tare_g
                          if product.unit_gross_g is not None else None),
    )


def requires_approval(default: int, requested: int, limit=DEFAULTS.booth_approval_limit) -> bool:
    default, requested = count(default), count(requested)
    limit = amount(limit, "approval limit")
    if limit > 1:
        raise ValueError("approval limit must be <= 1")
    # Cross multiplication preserves exact boundary semantics, including zero defaults.
    return Decimal(abs(requested - default)) > limit * default


def buffer_units(spreads: Iterable[Decimal], z=DEFAULTS.hub_buffer_z) -> int:
    """Ceil RSS in units, computed rationally to avoid sqrt/ceiling roundoff."""
    from fractions import Fraction

    values = [Fraction(amount(v, "forecast spread")) for v in spreads]
    factor = Fraction(amount(z, "buffer multiplier"))
    square = factor * factor * sum((v * v for v in values), Fraction(0))
    floor = isqrt(square.numerator // square.denominator)
    return floor if floor * floor * square.denominator == square.numerator else floor + 1


def hub_default(orders: Iterable[int], spreads: Iterable[Decimal], stock: int,
                z=DEFAULTS.hub_buffer_z) -> int:
    """Caller passes eligible, open booths only, with one spread per order."""
    orders, spreads = tuple(orders), tuple(spreads)
    if len(orders) != len(spreads):
        raise ValueError("one forecast spread is required per eligible order")
    return max(0, sum(count(n) for n in orders) + buffer_units(spreads, z) - count(stock))


def capped_spare_units(desired: int, stock: int, slots_left: int, grams_left: int,
                       product: Packaging, supported_lanes: frozenset) -> int:
    desired, stock = count(desired), count(stock)
    slots_left, grams_left = count(slots_left), count(grams_left)
    if product.lane not in supported_lanes:
        return 0
    slots = min(slots_left, grams_left // product.full_container_g)
    return min(desired, stock, slots * product.units_per_container)


def suggestion_units(p50) -> int:
    return int(amount(p50).to_integral_value(rounding=ROUND_CEILING))


def travel_minutes(km, speed_kmph=None, *, rain=False,
                   traffic=False, settings: Settings = DEFAULTS) -> Decimal:
    if speed_kmph is None:
        speed_kmph = settings.planning_speed_kmph
    speed = amount(speed_kmph, "speed", positive=True)
    if type(rain) is not bool or type(traffic) is not bool:
        raise ValueError("rain and traffic must be boolean segment flags")
    if rain:
        speed *= settings.rain_factor
    if traffic:
        speed *= settings.traffic_factor
    return amount(km, "distance") / speed * 60


def service_minutes(slots: int, settings: Settings = DEFAULTS) -> Decimal:
    return settings.dock_minutes + count(slots, "slots") * settings.minutes_per_slot


def stop_times(departure: datetime, travel_min, slots: int, *, opens_at: datetime | None = None,
               settings: Settings = DEFAULTS) -> tuple[datetime, datetime]:
    """Return arrival and service completion, including any receiving-window wait."""
    def duration(minutes):
        micros = (amount(minutes) * 60000000).to_integral_value(rounding=ROUND_CEILING)
        return timedelta(microseconds=int(micros))

    arrival = require_aware(departure) + duration(travel_min)
    service_start = max(arrival, require_aware(opens_at)) if opens_at else arrival
    return arrival, service_start + duration(service_minutes(slots, settings))


def slack_minutes(deadline: datetime, completed_at: datetime) -> Decimal:
    delta = require_aware(deadline) - require_aware(completed_at)
    microseconds = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    return Decimal(microseconds) / Decimal(60000000)


def risk(slack, settings: Settings = DEFAULTS) -> str:
    try:
        value = Decimal(str(slack))
    except InvalidOperation:
        raise ValueError("slack must be finite") from None
    if not value.is_finite():
        raise ValueError("slack must be finite")
    if value >= settings.risk_low_minutes:
        return "Low"
    if value >= settings.risk_medium_minutes:
        return "Medium"
    return "High" if value >= 0 else "Late"


def trip_cost(total_km, settings: Settings = DEFAULTS) -> tuple[Decimal, Decimal]:
    """Input includes return kilometres. Output is fuel litres, currency cost."""
    distance = amount(total_km)
    return distance / settings.fuel_km_per_l, distance * settings.cost_per_km


def utilisation(used: int, capacity: int) -> Decimal:
    used, capacity = count(used), count(capacity)
    if capacity == 0 or used > capacity:
        raise ValueError("capacity must be positive and cannot be exceeded")
    return Decimal(used) / capacity * 100
