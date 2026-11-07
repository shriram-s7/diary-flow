"""Online SQLite snapshot and non-overwriting restore; includes committed WAL data."""
from pathlib import Path
import sqlite3
from contextlib import closing


def inspect_backup(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("foreign key check failed")
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or not source.is_file():
        raise ValueError("source must exist; destination must be a different new path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents silently replacing a backup or a running DB.
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(str(destination))) as dst:
                src.backup(dst)
        inspect_backup(destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def restore(source, destination):
    """Restore into a NEW path only; operator changes config after verification."""
    inspect_backup(source)
    return snapshot(source, destination)
