"""An app wired to a fresh, migrated database, and helpers to add users."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert

from learning_tracker.db.models import User
from learning_tracker.web import ratelimit as ratelimit_module
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
        invite_key=b"v" * 32,
        reset_key=b"r" * 32,
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


# Rate-limit windows are aligned to the clock, so a 1-minute window can roll over in
# the middle of a test and reset its counts. Tests stretch every window to ten years
# (the counts, the order of checks and Retry-After stay real); the production values
# are pinned separately in tests/web/auth/test_rate_limits.py.
TEST_WINDOW = timedelta(days=3650)
LIMIT_NAMES = (
    "LOGIN_PER_IP",
    "PASSWORD_PER_ACCOUNT",
    "SIGNUP_PER_IP",
    "SIGNUP_PER_INSTANCE",
    "RESET_PER_IP",
)
PRODUCTION_LIMITS = {name: getattr(ratelimit_module, name) for name in LIMIT_NAMES}


@pytest.fixture(autouse=True)
def long_rate_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from learning_tracker.web import ratelimit
    from learning_tracker.web.auth import routes

    for name in LIMIT_NAMES:
        stretched = replace(getattr(ratelimit, name), window=TEST_WINDOW)
        monkeypatch.setattr(ratelimit, name, stretched)
        monkeypatch.setattr(routes, name, stretched)
