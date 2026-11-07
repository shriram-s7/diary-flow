"""Programmatic Alembic entry point; migrations are explicit, never startup resets."""
from pathlib import Path

from alembic import command
from alembic.config import Config

from .engine import unit_of_work


def migrate(engine, revision="head"):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with unit_of_work(engine) as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
