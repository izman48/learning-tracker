"""Without a mailer, an admin makes a single-use reset link: the newest link replaces
any older one, a used or expired link fails, and the reset signs out every device.
With a mailer the admin route answers 409: reset goes by email instead."""

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update

from learning_tracker.db.models import AdminAudit, PasswordReset, User
from learning_tracker.web.app import create_app
from learning_tracker.web.config import WebConfig
from tests.web.conftest import ORIGIN

PASSWORD = "correct horse battery"
NEW = "a fresh passphrase here"
CANT = "This link can't be used. It may have expired or been used already."


def sql(config: WebConfig, query: Any) -> list[Any]:
    engine = create_engine(config.database_url)
    with engine.begin() as conn:
        result = conn.execute(query)
        found = [tuple(r) for r in result.all()] if result.returns_rows else []
    engine.dispose()
    return found


def fresh(config: WebConfig) -> TestClient:
    return TestClient(create_app(config), base_url="https://testserver", headers={"origin": ORIGIN})


@pytest.fixture
def admin(client: TestClient, config: WebConfig, add_user: Callable[..., str]) -> TestClient:
    add_user("admin@example.com")
    sql(config, update(User).values(is_superuser=True))
    client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    return client


@pytest.fixture
def reader_id(config: WebConfig, add_user: Callable[..., str]) -> str:
    add_user("reader@example.com")
    [(user_id,)] = sql(config, select(User.id).where(User.email == "reader@example.com"))
    return str(user_id)


def make_link(admin: TestClient, user_id: str) -> str:
    response = admin.post(f"/api/admin/users/{user_id}/reset-link", json={})
    assert response.status_code == 201, response.text
    link: str = response.json()["link"]
    return link


def token_of(link: str) -> str:
    assert link.startswith(f"{ORIGIN}/reset#token=")  # fragment: never sent to a server
    return link.split("#token=", 1)[1]


def reset(client: TestClient, token: str, password: str = NEW) -> Any:
    return client.post("/api/auth/reset", json={"token": token, "new_password": password})


def test_people_list_shows_accounts_without_personal_reading_data(
    admin: TestClient, reader_id: str
) -> None:
    people = admin.get("/api/admin/users").json()

    assert {p["email"] for p in people} == {"admin@example.com", "reader@example.com"}
    assert set(people[0]) == {
        "id",
        "email",
        "created_at",
        "is_admin",
        "email_confirmed",
        "invite_label",
    }


def test_a_reset_link_works_once_and_signs_out_every_device(
    admin: TestClient, config: WebConfig, reader_id: str
) -> None:
    with fresh(config) as phone:
        phone.post("/api/auth/login", json={"email": "reader@example.com", "password": PASSWORD})
        assert phone.get("/api/me").status_code == 200  # precondition
        token = token_of(make_link(admin, reader_id))

        with fresh(config) as anyone:
            assert reset(anyone, token).status_code == 204
            assert reset(anyone, token).json() == {"detail": CANT}  # used
        assert phone.get("/api/me").status_code == 401  # every session ended

    with fresh(config) as anyone:
        ok = {"email": "reader@example.com", "password": NEW}
        assert anyone.post("/api/auth/login", json=ok).status_code == 204
    [(via,)] = sql(config, select(User.password_changed_via).where(User.id == reader_id))
    assert via == "admin_reset"
    assert sql(config, select(PasswordReset.user_id)) == []


def test_a_newer_link_revokes_the_older_one(
    admin: TestClient, config: WebConfig, reader_id: str
) -> None:
    older = token_of(make_link(admin, reader_id))
    newer = token_of(make_link(admin, reader_id))

    with fresh(config) as anyone:
        assert reset(anyone, older).json() == {"detail": CANT}
        assert reset(anyone, newer).status_code == 204


def test_an_expired_link_fails_by_the_database_clock(
    admin: TestClient, config: WebConfig, reader_id: str
) -> None:
    token = token_of(make_link(admin, reader_id))
    sql(config, update(PasswordReset).values(expires_at=text("now() - interval '1 minute'")))

    with fresh(config) as anyone:
        assert reset(anyone, token).json() == {"detail": CANT}


def test_link_lifetime_follows_the_setting(
    admin: TestClient, config: WebConfig, reader_id: str
) -> None:
    response = admin.post(f"/api/admin/users/{reader_id}/reset-link", json={})

    assert response.json()["expires_in_minutes"] == config.reset_token_minutes
    [(remaining,)] = sql(
        config, text("select extract(epoch from expires_at - now()) from password_resets")
    )
    assert config.reset_token_minutes * 60 - 60 < remaining <= config.reset_token_minutes * 60


@pytest.mark.parametrize("token", ["", "garbage", "x" * 43, "x" * 500])
def test_malformed_tokens_get_the_same_answer(client: TestClient, token: str) -> None:
    response = reset(client, token)

    assert response.status_code in (400, 422)
    if response.status_code == 400:
        assert response.json() == {"detail": CANT}


@pytest.mark.parametrize("password", ["short", "p" * 129])
def test_reset_password_length_is_checked(
    admin: TestClient, config: WebConfig, reader_id: str, password: str
) -> None:
    token = token_of(make_link(admin, reader_id))
    with fresh(config) as anyone:
        assert reset(anyone, token, password).status_code == 422
        assert reset(anyone, token).status_code == 204  # the token was not spent


def test_the_token_is_stored_only_as_a_keyed_hash(
    admin: TestClient, config: WebConfig, reader_id: str
) -> None:
    token = token_of(make_link(admin, reader_id))

    [(stored,)] = sql(config, select(PasswordReset.token_hash))
    assert len(stored) == 32 and token.encode() not in stored


def test_making_a_link_is_audited(admin: TestClient, config: WebConfig, reader_id: str) -> None:
    make_link(admin, reader_id)

    assert sql(config, select(AdminAudit.action, AdminAudit.target_id)) == [
        ("user.reset_link", reader_id)
    ]


def test_with_a_mailer_the_admin_link_is_refused(
    config: WebConfig, add_user: Callable[..., str], reader_id: str
) -> None:
    add_user("admin@example.com")
    sql(config, update(User).where(User.email == "admin@example.com").values(is_superuser=True))
    with fresh(replace(config, mailer_enabled=True)) as admin:
        admin.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})

        response = admin.post(f"/api/admin/users/{reader_id}/reset-link", json={})

    assert response.status_code == 409
    assert response.json() == {"detail": "reset_by_email"}
    assert sql(config, select(PasswordReset.user_id)) == []


def test_unknown_user_is_404(admin: TestClient) -> None:
    response = admin.post(
        "/api/admin/users/00000000-0000-7000-8000-000000000000/reset-link", json={}
    )
    assert response.status_code == 404


def test_reset_is_rate_limited_per_ip(client: TestClient) -> None:
    statuses = [reset(client, "x" * 43).status_code for _ in range(10)]
    assert set(statuses) == {400}  # precondition

    assert reset(client, "x" * 43).status_code == 429


def test_the_token_never_reaches_the_logs(
    admin: TestClient, config: WebConfig, reader_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    token = token_of(make_link(admin, reader_id))
    with fresh(config) as anyone:
        reset(anyone, token)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "auth.reset" in logged  # precondition: the reset was logged
    assert token not in logged and NEW not in logged
