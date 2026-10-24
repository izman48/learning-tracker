"""The baseline migration builds exactly the schema the models describe, and its
downgrade fully reverses it, on real Postgres with foreign keys enforced."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect

from learning_tracker.db.migrate import alembic_config
from learning_tracker.db.models import Base

BASELINE_TABLES = {
    "users",
    "sessions",
    "password_resets",
    "email_confirmations",
    "invite_codes",
    "invite_redemptions",
    "admin_audit",
    "rate_limit_buckets",
}


@pytest.fixture
def engine(empty_database_url: str) -> Iterator[Engine]:
    engine = create_engine(empty_database_url)
    yield engine
    engine.dispose()


def tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def test_upgrade_creates_the_baseline_tables(empty_database_url: str, engine: Engine) -> None:
    command.upgrade(alembic_config(empty_database_url), "head")

    assert tables(engine) == BASELINE_TABLES


def test_migrated_schema_matches_the_models(empty_database_url: str, engine: Engine) -> None:
    command.upgrade(alembic_config(empty_database_url), "head")

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)

    assert diff == []


def test_downgrade_removes_everything_and_upgrade_runs_again(
    empty_database_url: str, engine: Engine
) -> None:
    config = alembic_config(empty_database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")
    assert tables(engine) == set()
    with engine.connect() as conn:
        enums = conn.exec_driver_sql("select typname from pg_type where typtype = 'e'").all()
    assert enums == []

    command.upgrade(config, "head")
    assert tables(engine) == BASELINE_TABLES
