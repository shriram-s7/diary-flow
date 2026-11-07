"""Business-local schedule -> dated UTC instants. No mutable clock or scheduler."""
from datetime import date, datetime, timedelta, timezone

from .settings import DEFAULTS, Settings
from .types import Shift

# The prototype supports modern Chennai dates only; no historical timezone inference.
BUSINESS_ZONE = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")


def daily_events(delivery_date: date, settings: Settings = DEFAULTS) -> tuple[tuple[str, datetime], ...]:
    if type(delivery_date) is not date or delivery_date.year < 2000:
        raise ValueError("delivery_date must be a date in the modern prototype calendar (>=2000)")
    events = (
        ("suggestions", settings.suggestion_time, -1),
        ("booth_cutoff", settings.booth_cutoff, -1),
        ("hub_defaults", settings.hub_default_time, -1),
        ("hub_cutoff", settings.hub_cutoff, -1),
        ("plant_dispatch", settings.plant_dispatch, -1),
        ("hub_dispatch", settings.hub_dispatch, 0),
        ("booth_ready_by", settings.booth_ready_by, 0),
        ("day_close", settings.day_close, 0),
    )
    return tuple((name, datetime.combine(delivery_date + timedelta(days=offset), at,
                                         BUSINESS_ZONE).astimezone(timezone.utc))
                 for name, at, offset in events)


def shift_on(start_date: date, start_time, end_time) -> Shift:
    if type(start_date) is not date or start_date.year < 2000:
        raise ValueError("shift date must be a date >=2000")
    if start_time.tzinfo is not None or end_time.tzinfo is not None:
        raise ValueError("shift times must be business-local without timezone")
    if start_time == end_time:
        raise ValueError("equal shift times are ambiguous, not a 24-hour shift")
    end_date = start_date + timedelta(days=1 if end_time < start_time else 0)
    return Shift(start=datetime.combine(start_date, start_time, BUSINESS_ZONE).astimezone(timezone.utc),
                 end=datetime.combine(end_date, end_time, BUSINESS_ZONE).astimezone(timezone.utc))
