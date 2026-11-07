"""Relational catalogue, inventory, ordering, trips and delivery evidence."""
from alembic import op
from v2.db.schema_v1 import BUSINESS, metadata

revision = "0002_business"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    for table in metadata.sorted_tables:
        if table.name in BUSINESS:
            table.create(op.get_bind(), checkfirst=False)
    if op.get_bind().dialect.name == "sqlite":
        for table in ("stock_movements", "crate_movements", "day_closes", "close_adjustments"):
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} "
                           "BEGIN SELECT RAISE(ABORT, 'immutable ledger record'); END")
        for action in ("INSERT", "UPDATE"):
            op.execute(f"CREATE TRIGGER nonoverlap_trips_{action.lower()} BEFORE {action} ON trips "
                "WHEN NEW.state <> 'cancelled' AND EXISTS (SELECT 1 FROM trips t "
                "WHERE t.run_id=NEW.run_id AND t.id<>NEW.id AND t.state<>'cancelled' "
                "AND (t.vehicle_id=NEW.vehicle_id OR t.driver_id=NEW.driver_id) "
                "AND t.departure_at<NEW.return_at AND t.return_at>NEW.departure_at) "
                "BEGIN SELECT RAISE(ABORT, 'vehicle or driver trip overlap'); END")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup to a new path")
