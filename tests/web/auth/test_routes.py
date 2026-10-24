"""Our own auth routes: sign in, sign out, who am I, change password."""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from learning_tracker.web.app import create_app
from learning_tracker.web.auth.sessions import COOKIE_NAME
from learning_tracker.web.config import WebConfig
from tests.web.routes import api_routes

PASSWORD = "correct horse battery"
AddUser = Callable[..., str]


def session_rows(client: TestClient) -> int:
    """Count sessions through a separate connection, so an uncommitted change shows."""
    from sqlalchemy import create_engine, func, select

    from learning_tracker.db.models import Session

    engine = create_engine(client.app.state.config.database_url)  # type: ignore[attr-defined]
    with engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(Session)).scalar_one()
    engine.dispose()
    return count


def me_with_cookie(client: TestClient, token: str) -> int:
    """GET /api/me presenting only this session token."""
    client.cookies.clear()
    return client.get("/api/me", headers={"cookie": f"{COOKIE_NAME}={token}"}).status_code


def login(client: TestClient, email: str, password: str = PASSWORD) -> Any:
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_sign_in_sets_the_session_cookie_and_me_works(
    client: TestClient, add_user: AddUser
) -> None:
    add_user()

    response = login(client, "reader@example.com")

    assert response.status_code == 204
    assert COOKIE_NAME in response.cookies
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {
        "email": "reader@example.com",
        "timezone": "Europe/London",
        "is_admin": False,
        "email_confirmed": False,
    }


def test_email_is_matched_ignoring_case(client: TestClient, add_user: AddUser) -> None:
    add_user("Reader@Example.com")

    assert login(client, "reader@EXAMPLE.com").status_code == 204


def test_unknown_email_and_wrong_password_look_identical(
    client: TestClient, add_user: AddUser
) -> None:
    add_user()

    unknown = login(client, "nobody@example.com")
    wrong = login(client, "reader@example.com", "not the password")

    assert unknown.status_code == wrong.status_code == 400
    assert unknown.content == wrong.content
    assert unknown.json() == {"detail": "Email or password is wrong"}
    assert COOKIE_NAME not in unknown.cookies and COOKIE_NAME not in wrong.cookies


def test_inactive_user_cannot_sign_in(client: TestClient, add_user: AddUser) -> None:
    add_user()
    from sqlalchemy import create_engine, update

    from learning_tracker.db.models import User

    engine = create_engine(str(client.app.state.config.database_url))  # type: ignore[attr-defined]
    with engine.begin() as conn:
        conn.execute(update(User).values(is_active=False))
    engine.dispose()

    assert login(client, "reader@example.com").status_code == 400


def test_me_without_a_session_is_401(client: TestClient) -> None:
    assert client.get("/api/me").status_code == 401


def test_signing_in_again_rotates_the_session(client: TestClient, add_user: AddUser) -> None:
    add_user()
    login(client, "reader@example.com")
    first = client.cookies[COOKIE_NAME]

    login(client, "reader@example.com")
    second = client.cookies[COOKIE_NAME]

    assert second != first
    assert session_rows(client) == 1  # the old session is gone, the new one committed
    assert me_with_cookie(client, second) == 200  # precondition: the helper works
    assert me_with_cookie(client, first) == 401


def test_sign_out_ends_the_session_and_clears_the_device(
    client: TestClient, add_user: AddUser
) -> None:
    add_user()
    login(client, "reader@example.com")
    token = client.cookies[COOKIE_NAME]

    response = client.post("/api/auth/logout", json={})

    assert response.status_code == 204
    assert response.headers["clear-site-data"] == '"cache", "storage"'
    assert f"{COOKIE_NAME}=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert me_with_cookie(client, token) == 401
    assert session_rows(client) == 0  # committed, read back through a fresh connection


def test_change_password_needs_the_current_one(client: TestClient, add_user: AddUser) -> None:
    add_user()
    login(client, "reader@example.com")

    response = client.post(
        "/api/me/password",
        json={"current_password": "wrong one", "new_password": "a brand new passphrase"},
    )

    assert response.status_code == 400
    assert login(client, "reader@example.com").status_code == 204  # old password still works


def test_change_password_ends_other_sessions_and_rotates_this_one(
    config: WebConfig, client: TestClient, add_user: AddUser
) -> None:
    add_user()
    with TestClient(
        create_app(config), base_url="https://testserver", headers={"origin": config.public_origin}
    ) as phone:
        login(phone, "reader@example.com")
        login(client, "reader@example.com")
        before = client.cookies[COOKIE_NAME]

        response = client.post(
            "/api/me/password",
            json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
        )

        assert response.status_code == 204
        assert client.get("/api/me").status_code == 200  # this device stays signed in
        assert phone.get("/api/me").status_code == 401  # every other session ended
        after = client.cookies[COOKIE_NAME]
        assert after != before
        assert me_with_cookie(client, before) == 401  # a captured old cookie is dead
        assert session_rows(client) == 1
    assert login(client, "reader@example.com", "a brand new passphrase").status_code == 204
    assert login(client, "reader@example.com").status_code == 400


@pytest.mark.parametrize(
    ("password", "ok"),
    [("x" * 9, False), ("x" * 10, True), ("x" * 128, True), ("x" * 129, False)],
)
def test_new_password_must_be_10_to_128_characters(
    client: TestClient, add_user: AddUser, password: str, ok: bool
) -> None:
    add_user()
    login(client, "reader@example.com")

    response = client.post(
        "/api/me/password", json={"current_password": PASSWORD, "new_password": password}
    )

    assert response.status_code == (204 if ok else 422)


def test_oversized_login_password_is_refused_before_hashing(client: TestClient) -> None:
    response = login(client, "reader@example.com", "x" * 129)

    assert response.status_code == 422


@pytest.mark.parametrize("field", ["is_superuser", "is_active", "is_verified", "id"])
def test_extra_fields_are_refused(client: TestClient, add_user: AddUser, field: str) -> None:
    add_user()
    body = {"email": "reader@example.com", "password": PASSWORD, field: True}

    assert client.post("/api/auth/login", json=body).status_code == 422


def test_no_route_accepts_privilege_fields(config: WebConfig) -> None:
    routes = list(api_routes(create_app(config).routes))
    assert any(r.path == "/api/auth/login" for r in routes)  # precondition

    for route in routes:
        body = route.body_field
        if body is None:
            continue
        fields = set(body.field_info.annotation.model_fields)  # type: ignore[union-attr]
        assert not fields & {"is_superuser", "is_active", "is_verified"}, route.path


def test_auth_logs_hold_no_email_password_or_token(
    client: TestClient, add_user: AddUser, caplog: pytest.LogCaptureFixture
) -> None:
    add_user()
    caplog.set_level(logging.DEBUG)

    login(client, "reader@example.com", "not the password")
    login(client, "reader@example.com")
    token = client.cookies[COOKIE_NAME]
    client.post(
        "/api/me/password",
        json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
    )
    client.post("/api/auth/logout", json={})

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "auth.login_failed" in logged and "auth.login" in logged  # precondition
    for secret in ("reader@example.com", PASSWORD, "not the password", "brand new", token):
        assert secret not in logged


@pytest.mark.parametrize(
    "body",
    [
        {"email": "reader@example.com", "password": "p" * 129},  # too long
        {"email": "r", "password": "hunter-two-secret"},  # bad email, password valid
        {"email": "reader@example.com", "password": "hunter-two-secret", "is_superuser": True},
    ],
)
def test_validation_errors_never_echo_what_was_submitted(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post("/api/auth/login", json=body)

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert errors and all("loc" in e and "msg" in e for e in errors)  # precondition
    assert "hunter-two-secret" not in response.text and "p" * 129 not in response.text
    assert all("input" not in e and "ctx" not in e for e in errors)


def test_change_password_records_when_and_how(client: TestClient, add_user: AddUser) -> None:
    from sqlalchemy import create_engine, select

    from learning_tracker.db.models import User

    add_user()
    login(client, "reader@example.com")
    client.post(
        "/api/me/password",
        json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
    )

    engine = create_engine(client.app.state.config.database_url)  # type: ignore[attr-defined]
    with engine.connect() as conn:
        changed_at, via = conn.execute(
            select(User.password_changed_at, User.password_changed_via)
        ).one()
    engine.dispose()
    assert changed_at is not None
    assert via == "user"
