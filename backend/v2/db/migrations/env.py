"""Migration connections must be supplied by the explicit v2 migration command."""
from alembic import context
from v2.db.schema import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use python -m v2.db --database PATH migrate")
context.configure(connection=connection, target_metadata=metadata,
                  compare_type=True, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
