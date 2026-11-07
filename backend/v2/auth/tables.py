from sqlalchemy import Table, Column, String, Text, ForeignKey, Boolean
from v2.db.schema_v1 import metadata
from v2.db.sqltypes import ExactInteger, UTCInstant

attempts = Table("login_limits", metadata,
    Column("key", String(64), primary_key=True),
    Column("count", ExactInteger(), nullable=False),
    Column("reset_at", UTCInstant(), nullable=False))
auth_audit = Table("auth_audit", metadata,
    Column("id", String(128), primary_key=True),
    Column("actor_id", String(128), ForeignKey("users.id")),
    Column("effective_actor_id", String(128), ForeignKey("users.id")),
    Column("kind", String(64), nullable=False),
    Column("at", UTCInstant(), nullable=False))
demo_users = Table("demo_users", metadata,
    Column("user_id", String(128), ForeignKey("users.id"), primary_key=True),
    Column("label", String(128), nullable=False))
