"""An app wired to a fresh, migrated database, and helpers to add users."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert

from learning_tracker.db.models import User
from learning_tracker.web.app import create_app
from learning_tracker.web.auth.passwords import password_helper
from learning_tracker.web.config import WebConfig

SESSION_KEY = b"k" * 32
ORIGIN = "https://reads.example.com"


@pytest.fixture
def config(migrated_database_url: str) -> WebConfig:
    return WebConfig(
        database_url=migrated_database_url,
        session_key=SESSION_KEY,
        session_lifetime=timedelta(days=30),
        public_origin=ORIGIN,
        ip_key=b"i" * 32,
    )


@pytest.fixture
def client(config: WebConfig) -> Iterator[TestClient]:
    # https, because the browser (and httpx) only send a Secure cookie over https.
    # The Origin header is what our own PWA sends; CSRF tests override or drop it.
    with TestClient(
        create_app(config), base_url="https://testserver", headers={"origin": ORIGIN}
    ) as client:
        yield client


@pytest.fixture
def add_user(migrated_database_url: str) -> Iterator[object]:
    engine = create_engine(migrated_database_url)

    def add(email: str = "reader@example.com", password: str = "correct horse battery") -> str:
        with engine.begin() as conn:
            conn.execute(
                insert(User).values(
                    email=email,
                    hashed_password=password_helper.hash(password),
                    timezone="Europe/London",
                )
            )
        return email

    yield add
    engine.dispose()
