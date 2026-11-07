"""Persistent authentication throttles, demo markers and security audit."""
from alembic import op
import sqlalchemy as sa
from v2.db.sqltypes import ExactInteger, UTCInstant

revision = "0003_auth"
down_revision = "0002_business"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("login_limits", sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("count", ExactInteger(), nullable=False), sa.Column("reset_at", UTCInstant(), nullable=False),
        sa.CheckConstraint("count >= 0", name="ck_login_count"))
    op.create_table("auth_audit", sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), sa.ForeignKey("users.id")),
        sa.Column("effective_actor_id", sa.String(128), sa.ForeignKey("users.id")),
        sa.Column("kind", sa.String(64), nullable=False), sa.Column("at", UTCInstant(), nullable=False))
    op.create_table("demo_users", sa.Column("user_id", sa.String(128), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("label", sa.String(128), nullable=False))
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER immutable_auth_audit_{action.lower()} BEFORE {action} ON auth_audit "
                       "BEGIN SELECT RAISE(ABORT,'immutable auth audit'); END")


def downgrade():
    raise RuntimeError("Restore a verified backup to a new path instead")
