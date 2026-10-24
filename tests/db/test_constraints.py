"""Constraints the database itself enforces, so no code path can skip them."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, delete, insert, select
from sqlalchemy.exc import IntegrityError

from learning_tracker.db.migrate import alembic_config
from learning_tracker.db.models import InviteCode, Session, User


@pytest.fixture
def engine(empty_database_url: str) -> Iterator[Engine]:
    command.upgrade(alembic_config(empty_database_url), "head")
    engine = create_engine(empty_database_url)
    yield engine
    engine.dispose()


def add_user(engine: Engine, email: str) -> uuid.UUID:
    with engine.begin() as conn:
        return conn.execute(
            insert(User).values(email=email, hashed_password="x", timezone="UTC").returning(User.id)
        ).scalar_one()


def test_user_ids_are_uuid7_by_default(engine: Engine) -> None:
    user_id = add_user(engine, "a@example.com")

    assert user_id.version == 7


def test_emails_are_unique_ignoring_case(engine: Engine) -> None:
    add_user(engine, "Reader@example.com")

    with pytest.raises(IntegrityError):
        add_user(engine, "reader@EXAMPLE.com")


@pytest.mark.parametrize(("max_uses", "uses"), [(0, 0), (1001, 0), (3, 4), (3, -1)])
def test_invite_code_use_counts_are_bounded(engine: Engine, max_uses: int, uses: int) -> None:
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(InviteCode).values(code_hash=b"h" * 32, max_uses=max_uses, uses=uses))


def test_valid_invite_code_is_accepted(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(insert(InviteCode).values(code_hash=b"h" * 32, max_uses=3, uses=3))


def test_deleting_a_user_deletes_their_sessions(engine: Engine) -> None:
    user_id = add_user(engine, "a@example.com")
    with engine.begin() as conn:
        conn.execute(
            insert(Session).values(
                token_hash=b"t" * 32, user_id=user_id, expires_at=datetime(2030, 1, 1, tzinfo=UTC)
            )
        )
        conn.execute(delete(User).where(User.id == user_id))

    with engine.connect() as conn:
        assert conn.execute(select(Session)).all() == []
