"""Append-only effective product and settings snapshots; no historical rewrites."""
from alembic import op
import sqlalchemy as sa
from v2.db.sqltypes import ExactInteger

revision = '0004_catalog'
down_revision = '0003_auth'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('catalog_revisions',
        sa.Column('run_id', sa.String(128), primary_key=True),
        sa.Column('kind', sa.String(32), primary_key=True),
        sa.Column('entity_id', sa.String(128), primary_key=True),
        sa.Column('revision', ExactInteger(), primary_key=True),
        sa.Column('effective_date', sa.Date(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('deleted', sa.Boolean(), nullable=False),
        sa.Column('command_id', sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(['run_id', 'command_id'], ['commands.run_id', 'commands.id']),
        sa.CheckConstraint('revision >= 0', name='catalog_revision_nonnegative'),
        sa.CheckConstraint("kind IN ('product','settings')", name='catalog_kind'))
    if op.get_bind().dialect.name == 'sqlite':
        for action in ('UPDATE', 'DELETE'):
            op.execute(f'CREATE TRIGGER immutable_catalog_{action.lower()} BEFORE {action} ON catalog_revisions '
                       "BEGIN SELECT RAISE(ABORT,'immutable catalog revision'); END")


def downgrade():
    raise RuntimeError('Restore a verified backup to a new path instead')
