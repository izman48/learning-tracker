"""Shared fixtures. Database tests run against real Postgres (FKs and constraints on).

Set TEST_DATABASE_URL to a Postgres server URL whose user may CREATE DATABASE, e.g.
postgresql+psycopg://postgres:devonly@127.0.0.1:55432/postgres. Each test gets a
fresh, empty database, which is dropped afterwards. The variable deliberately has
no LT_ prefix: the app refuses unknown LT_* settings at startup.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


def server_url() -> URL:
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.fail("TEST_DATABASE_URL is not set; database tests need a real Postgres")
    return make_url(raw)


@pytest.fixture
def empty_database_url() -> Iterator[str]:
    """URL of a brand-new empty database, dropped after the test."""
    admin_url = server_url()
    name = f"lt_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield admin_url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def migrated_database_url(empty_database_url: str) -> str:
    """A fresh database with every migration applied."""
    from alembic import command

    from learning_tracker.db.migrate import alembic_config

    command.upgrade(alembic_config(empty_database_url), "head")
    return empty_database_url


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
