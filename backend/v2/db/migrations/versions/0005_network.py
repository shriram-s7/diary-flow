"""Append-only dated network snapshots and road-validation evidence."""
from alembic import op
import sqlalchemy as sa
from v2.db.sqltypes import ExactInteger

revision = '0005_network'
down_revision = '0004_catalog'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('node_revisions',
        sa.Column('run_id', sa.String(128), primary_key=True),
        sa.Column('entity_id', sa.String(128), primary_key=True),
        sa.Column('revision', ExactInteger(), primary_key=True),
        sa.Column('effective_date', sa.Date(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('deleted', sa.Boolean(), nullable=False),
        sa.Column('command_id', sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(['run_id', 'command_id'], ['commands.run_id', 'commands.id']),
        sa.ForeignKeyConstraint(['run_id', 'entity_id'], ['nodes.run_id', 'nodes.id']),
        sa.CheckConstraint('revision >= 0', name='node_revision_nonnegative'))
    if op.get_bind().dialect.name == 'sqlite':
        for action in ('UPDATE', 'DELETE'):
            op.execute(f'CREATE TRIGGER immutable_node_{action.lower()} BEFORE {action} ON node_revisions '
                       "BEGIN SELECT RAISE(ABORT,'immutable node revision'); END")


def downgrade():
    raise RuntimeError('Restore a verified backup to a new path instead')
