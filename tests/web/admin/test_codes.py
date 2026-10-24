"""Admin: make, list and turn off invite codes. Non-admins see 404 on every admin route."""

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update

from learning_tracker.db.models import AdminAudit, InviteCode, User
from learning_tracker.web.app import create_app
from learning_tracker.web.config import WebConfig
from tests.web.conftest import ORIGIN
from tests.web.routes import api_routes

PASSWORD = "correct horse battery"


def sql(config: WebConfig, query: Any) -> list[Any]:
    engine = create_engine(config.database_url)
    with engine.begin() as conn:
        result = conn.execute(query)
        found = [tuple(r) for r in result.all()] if result.returns_rows else []
    engine.dispose()
    return found


@pytest.fixture
def admin(client: TestClient, config: WebConfig, add_user: Callable[..., str]) -> TestClient:
    add_user("admin@example.com")
    sql(config, update(User).values(is_superuser=True))
    client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    return client


@pytest.fixture
def reader(config: WebConfig, add_user: Callable[..., str]) -> Iterator[TestClient]:
    add_user("reader@example.com")
    with TestClient(
        create_app(config), base_url="https://testserver", headers={"origin": ORIGIN}
    ) as client:
        client.post("/api/auth/login", json={"email": "reader@example.com", "password": PASSWORD})
        yield client


def admin_routes(config: WebConfig) -> list[tuple[str, str]]:
    return sorted(
        (m, r.path)
        for r in api_routes(create_app(config).routes)
        if r.path.startswith("/api/admin/")
        for m in r.methods or set()
    )


def call(client: TestClient, method: str, path: str) -> int:
    some_id = "00000000-0000-7000-8000-000000000000"
    concrete = path.replace("{code_id}", some_id).replace("{user_id}", some_id)
    return client.request(method, concrete, json={}).status_code


def test_every_admin_route_is_404_for_readers_and_strangers(
    config: WebConfig, reader: TestClient
) -> None:
    routes = admin_routes(config)
    assert ("POST", "/api/admin/codes") in routes  # precondition: the walk finds them

    with TestClient(
        create_app(config), base_url="https://testserver", headers={"origin": ORIGIN}
    ) as stranger:
        for method, path in routes:
            assert call(reader, method, path) == 404, (method, path)
            assert call(stranger, method, path) == 404, (method, path)


def test_admin_makes_a_code_that_works_once_by_default(
    admin: TestClient, config: WebConfig
) -> None:
    made = admin.post("/api/admin/codes", json={"label": "Book club"})

    assert made.status_code == 201
    body = made.json()
    assert set(body) == {"id", "code", "label", "max_uses", "expires_at"}
    assert body["max_uses"] == 1 and body["label"] == "Book club"
    [(remaining,)] = sql(config, text("select expires_at - now() from invite_codes"))
    assert timedelta(days=30) - timedelta(minutes=1) < remaining <= timedelta(days=30)

    admin.post("/api/auth/logout", json={})
    signup = {"email": "new@example.com", "password": PASSWORD, "timezone": "UTC"}
    assert admin.post("/api/auth/signup", json={**signup, "code": body["code"]}).status_code == 201


def test_the_plain_code_is_never_stored_or_listed(admin: TestClient, config: WebConfig) -> None:
    code = admin.post("/api/admin/codes", json={}).json()["code"]

    listed = admin.get("/api/admin/codes")
    assert listed.status_code == 200 and len(listed.json()) == 1  # precondition
    assert code not in listed.text
    [(stored,)] = sql(config, select(InviteCode.code_hash))
    assert code.encode() not in stored and code.replace("-", "").encode() not in stored


@pytest.mark.parametrize(
    "body",
    [{"max_uses": 0}, {"max_uses": 1001}, {"expires_in_days": 0}, {"expires_in_days": 366},
     {"label": "x" * 81}, {"uses": 5}, {"code_hash": "00"}],
)  # fmt: skip
def test_bad_code_requests_are_refused(admin: TestClient, body: dict[str, Any]) -> None:
    assert admin.post("/api/admin/codes", json=body).status_code == 422


def test_list_shows_state_and_who_used_each_code(admin: TestClient, config: WebConfig) -> None:
    made = admin.post("/api/admin/codes", json={"label": "Friends", "max_uses": 2}).json()
    admin.post("/api/auth/logout", json={})
    admin.post(
        "/api/auth/signup",
        json={
            "email": "a@example.com",
            "password": PASSWORD,
            "timezone": "UTC",
            "code": made["code"],
        },
    )
    admin.post("/api/auth/logout", json={})
    admin.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})

    [row] = admin.get("/api/admin/codes").json()

    assert row["label"] == "Friends" and row["uses"] == 1 and row["max_uses"] == 2
    assert row["state"] == "active"
    assert [r["email"] for r in row["redeemed_by"]] == ["a@example.com"]


def test_turning_a_code_off_stops_it_and_is_audited(admin: TestClient, config: WebConfig) -> None:
    made = admin.post("/api/admin/codes", json={"max_uses": 5}).json()

    assert admin.post(f"/api/admin/codes/{made['id']}/disable", json={}).status_code == 204

    [row] = admin.get("/api/admin/codes").json()
    assert row["state"] == "off"
    actions = [a for (a,) in sql(config, select(AdminAudit.action).order_by(AdminAudit.id))]
    assert actions == ["code.create", "code.disable"]
    admin.post("/api/auth/logout", json={})
    signup = {"email": "late@example.com", "password": PASSWORD, "timezone": "UTC"}
    assert admin.post("/api/auth/signup", json={**signup, "code": made["code"]}).status_code == 400


@pytest.mark.parametrize(
    ("setup", "state"),
    [
        ({"uses": 1, "max_uses": 1}, "used_up"),
        ({"expires_at": text("now() - interval '1 minute'")}, "expired"),
    ],
)
def test_used_up_and_expired_states(
    admin: TestClient, config: WebConfig, setup: dict[str, Any], state: str
) -> None:
    admin.post("/api/admin/codes", json={})
    sql(config, update(InviteCode).values(**setup))

    assert admin.get("/api/admin/codes").json()[0]["state"] == state


def test_disabling_an_unknown_code_is_404(admin: TestClient) -> None:
    assert (
        admin.post(
            "/api/admin/codes/00000000-0000-7000-8000-000000000000/disable", json={}
        ).status_code
        == 404
    )
