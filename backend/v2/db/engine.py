"""Explicit local SQLite engine and short transaction boundaries."""
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL


def open_database(path: str | Path, *, busy_timeout_ms: int = 3000):
    if type(busy_timeout_ms) is not int or not 0 <= busy_timeout_ms <= 30000:
        raise ValueError("busy_timeout_ms must be an integer from 0 to 30000")
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)),
                           connect_args={"timeout": busy_timeout_ms / 1000})

    @event.listens_for(engine, "connect")
    def configure(dbapi, record):
        # Explicit BEGIN avoids sqlite3's legacy nontransactional DDL behavior.
        dbapi.isolation_level = None
        cursor = dbapi.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin(connection):
        # Reads remain concurrent in WAL; writers serialize before reading state.
        mode = "BEGIN IMMEDIATE" if connection.get_execution_options().get("v2_write") else "BEGIN"
        connection.exec_driver_sql(mode)

    return engine


@contextmanager
def unit_of_work(engine):
    """One connection per transaction/caller, never shared between threads.

    No network calls inside this boundary. A raised exception rolls back all effects.
    """
    with engine.connect().execution_options(v2_write=True) as connection:
        with connection.begin():
            yield connection
