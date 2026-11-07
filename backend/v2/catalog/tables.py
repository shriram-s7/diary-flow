from sqlalchemy import Table, Column, String, Date, JSON, Boolean, ForeignKeyConstraint, CheckConstraint
from v2.db.schema import metadata
from v2.db.sqltypes import ExactInteger


revisions = Table('catalog_revisions', metadata,
    Column('run_id', String(128), primary_key=True),
    Column('kind', String(32), primary_key=True),
    Column('entity_id', String(128), primary_key=True),
    Column('revision', ExactInteger(), primary_key=True),
    Column('effective_date', Date, nullable=False),
    Column('snapshot', JSON, nullable=False),
    Column('deleted', Boolean, nullable=False),
    Column('command_id', String(128), nullable=False),
    ForeignKeyConstraint(['run_id', 'command_id'], ['commands.run_id', 'commands.id']),
    CheckConstraint('revision >= 0', name='catalog_revision_nonnegative'),
    CheckConstraint("kind IN ('product','settings')", name='catalog_kind'))

node_revisions = Table('node_revisions', metadata,
    Column('run_id', String(128), primary_key=True),
    Column('entity_id', String(128), primary_key=True),
    Column('revision', ExactInteger(), primary_key=True),
    Column('effective_date', Date, nullable=False),
    Column('snapshot', JSON, nullable=False),
    Column('deleted', Boolean, nullable=False),
    Column('command_id', String(128), nullable=False),
    ForeignKeyConstraint(['run_id', 'command_id'], ['commands.run_id', 'commands.id']),
    ForeignKeyConstraint(['run_id', 'entity_id'], ['nodes.run_id', 'nodes.id']),
    CheckConstraint('revision >= 0', name='node_revision_nonnegative'))

fleet_revisions = Table('fleet_revisions', metadata,
    Column('run_id', String(128), primary_key=True),
    Column('kind', String(32), primary_key=True),
    Column('entity_id', String(128), primary_key=True),
    Column('revision', ExactInteger(), primary_key=True),
    Column('effective_date', Date, nullable=False),
    Column('snapshot', JSON, nullable=False),
    Column('deleted', Boolean, nullable=False),
    Column('command_id', String(128), nullable=False),
    ForeignKeyConstraint(['run_id', 'command_id'], ['commands.run_id', 'commands.id']),
    CheckConstraint('revision >= 0', name='fleet_revision_nonnegative'),
    CheckConstraint("kind IN ('vehicle','driver')", name='fleet_kind'))
