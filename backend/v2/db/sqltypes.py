"""Portable exact integers and UTC instants (epoch microseconds, never floats)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger
from sqlalchemy.types import TypeDecorator

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class ExactInteger(TypeDecorator):
    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and (type(value) is not int or not -(2**63) <= value < 2**63):
            raise ValueError("expected signed 64-bit integer, not a coerced count")
        return value


class UTCInstant(TypeDecorator):
    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC instant requires an aware datetime")
        delta = value.astimezone(timezone.utc) - EPOCH
        return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds

    def process_result_value(self, value, dialect):
        return None if value is None else EPOCH + timedelta(microseconds=value)
