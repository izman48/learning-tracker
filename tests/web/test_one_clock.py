"""Every expiry is set and checked on the database's clock.

The app host and the database can disagree about the time (a Docker VM clock was
15 minutes behind its host while this was written). A comparison between a Python
timestamp and the database's now() then gives the wrong answer. These tests run with
Python's clock pushed 15 minutes behind the database's, and the answers must not move.
"""

import importlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, update

from learning_tracker.db.models import Session
from learning_tracker.web.auth.sessions import COOKIE_NAME
from learning_tracker.web.config import WebConfig
from tests.web.conftest import TEST_WINDOW

SKEW = timedelta(minutes=15)
MODULES = (
    "learning_tracker.web.auth.sessions",
    "learning_tracker.web.auth.routes",
    "learning_tracker.web.ratelimit",
)


class BehindDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> BehindDatetime:
        real = datetime.now(tz or UTC)
        return cls.fromtimestamp((real - SKEW).timestamp(), tz or UTC)


@pytest.fixture
def python_clock_behind(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in MODULES:
        monkeypatch.setattr(
            importlib.import_module(name), "datetime", BehindDatetime, raising=False
        )
    yield


def run_sql(config: WebConfig, statement: Any) -> None:
    engine = create_engine(config.database_url)
    with engine.begin() as conn:
        conn.execute(statement)
    engine.dispose()


def test_a_session_expired_by_the_database_clock_is_expired(
    python_clock_behind: None, client: TestClient, config: WebConfig, add_user: Any
) -> None:
    add_user()
    client.post(
        "/api/auth/login", json={"email": "reader@example.com", "password": "correct horse battery"}
    )
    assert client.get("/api/me").status_code == 200  # precondition: signed in
    run_sql(config, update(Session).values(expires_at=text("now() - interval '1 minute'")))

    assert client.get("/api/me").status_code == 401


def test_a_new_session_lasts_its_lifetime_on_the_database_clock(
    python_clock_behind: None, client: TestClient, config: WebConfig, add_user: Any
) -> None:
    add_user()
    client.post(
        "/api/auth/login", json={"email": "reader@example.com", "password": "correct horse battery"}
    )
    assert COOKIE_NAME in client.cookies  # precondition

    engine = create_engine(config.database_url)
    with engine.connect() as conn:
        remaining = conn.execute(text("select expires_at - now() from sessions")).scalar_one()
    engine.dispose()
    assert config.session_lifetime - timedelta(minutes=1) < remaining <= config.session_lifetime


def test_rate_limit_retry_after_comes_from_the_database_clock(
    python_clock_behind: None, client: TestClient
) -> None:
    for i in range(5):
        client.post("/api/auth/login", json={"email": f"u{i}@example.com", "password": "x"})

    response = client.post("/api/auth/login", json={"email": "x@example.com", "password": "x"})

    assert response.status_code == 429
    assert 0 < int(response.headers["retry-after"]) <= TEST_WINDOW.total_seconds()
