"""Run from backend: python -m v2.db --database PATH migrate|check|backup|restore."""
import argparse
from pathlib import Path

from .backup import inspect_backup, restore, snapshot
from .engine import open_database
from .migrate import migrate


def main():
    parser = argparse.ArgumentParser(description="Explicit v2 database maintenance (v1 untouched)")
    parser.add_argument("--database", required=True, type=Path)
    commands = parser.add_subparsers(dest="action", required=True)
    migration = commands.add_parser("migrate")
    migration.add_argument("--revision", default="head", choices=("head", "0001_foundation", "0002_business", "0003_auth", "0004_catalog", "0005_network", "0006_fleet"))
    commands.add_parser("check")
    for action in ("backup", "restore"):
        sub = commands.add_parser(action)
        sub.add_argument("destination", type=Path, help="new path only; existing files are never replaced")
    args = parser.parse_args()
    if args.action == "migrate":
        engine = open_database(args.database)
        try:
            migrate(engine, args.revision)
        finally:
            engine.dispose()
        print(f"Database ready: {args.database.resolve()}")
    elif args.action == "check":
        print(f"Integrity OK; revision {inspect_backup(args.database)}")
    else:
        operation = snapshot if args.action == "backup" else restore
        print(operation(args.database, args.destination))


if __name__ == "__main__":
    main()
