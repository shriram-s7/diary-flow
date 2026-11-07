"""Run isolation, identity storage, atomic commands, outbox and event journal."""
from alembic import op
from v2.db.schema_v1 import FOUNDATION, metadata

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    for table in metadata.sorted_tables:
        if table.name in FOUNDATION:
            table.create(op.get_bind(), checkfirst=False)
    if op.get_bind().dialect.name == "sqlite":
        for table in ("audit", "scheduled_events", "commands"):
            condition = " WHEN OLD.completed = 1" if table == "commands" else ""
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table}"
                           f"{condition} BEGIN SELECT RAISE(ABORT, 'immutable committed record'); END")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup to a new path")
