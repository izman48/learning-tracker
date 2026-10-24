"""Run Alembic migrations from code (the `migrate` service and tests)."""

from pathlib import Path

from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Alembic reads options through ConfigParser, so a literal % must be doubled.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config
