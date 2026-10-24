"""Sign-up with an invite code: one shared message for every unusable code, atomic
redemption that a race cannot overrun, and nothing consumed when sign-up fails."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, insert, select, text

from learning_tracker.db.engine import make_engine, make_session_factory
from learning_tracker.db.models import InviteCode, InviteRedemption, User
from learning_tracker.web.app import create_app
from learning_tracker.web.auth.invites import code_hash, new_code
from learning_tracker.web.auth.sessions import COOKIE_NAME
from learning_tracker.web.auth.signup import SignupRefused, create_account
from learning_tracker.web.config import WebConfig
from tests.web.conftest import ORIGIN

CODE_REFUSED = "This code can't be used. Check it with the person who sent it."
NOT_CREATED = "We couldn't create your account. If you already have one, sign in instead."
CLOSED = "This app isn't taking new accounts right now."
PASSWORD = "correct horse battery"


@pytest.fixture
def make_code(config: WebConfig) -> Callable[..., str]:
    engine = create_engine(config.database_url)

    def make(max_uses: int = 1, uses: int = 0, **fields: Any) -> str:
        code = new_code()
        with engine.begin() as conn:
            conn.execute(
                insert(InviteCode).values(
                    code_hash=code_hash(config.invite_key, code),
                    max_uses=max_uses,
                    uses=uses,
                    **fields,
                )
            )
        return code

    return make


def scalar(config: WebConfig, query: Any) -> Any:
    engine = create_engine(config.database_url)
    with engine.connect() as conn:
        value = conn.execute(query).scalar_one()
    engine.dispose()
    return value


def signup(
    client: TestClient, code: str | None, email: str = "new@example.com", **extra: Any
) -> Any:
    body: dict[str, Any] = {"email": email, "password": PASSWORD, "timezone": "Europe/London"}
    if code is not None:
        body["code"] = code
    return client.post("/api/auth/signup", json={**body, **extra})


def test_a_valid_code_creates_a_signed_in_account(
    client: TestClient, config: WebConfig, make_code: Callable[..., str]
) -> None:
    response = signup(client, make_code())

    assert response.status_code == 201
    assert COOKIE_NAME in response.cookies
    assert client.get("/api/me").json()["email"] == "new@example.com"
    assert scalar(config, select(InviteCode.uses)) == 1
    assert scalar(config, select(func.count()).select_from(InviteRedemption)) == 1


def test_every_unusable_code_gets_the_same_bytes(
    client: TestClient, config: WebConfig, make_code: Callable[..., str]
) -> None:
    # Set on the database's clock, the clock the check uses.
    past = text("now() - interval '1 minute'")
    codes = {
        "wrong": new_code(),
        "used_up": make_code(max_uses=2, uses=2),
        "expired": make_code(expires_at=past),
        "turned_off": make_code(disabled_at=past),
        "malformed": "not-a-code",
    }

    responses = {kind: signup(client, code, f"{kind}@example.com") for kind, code in codes.items()}

    bodies = {r.content for r in responses.values()}
    assert {k: r.status_code for k, r in responses.items()} == dict.fromkeys(codes, 400)
    assert len(bodies) == 1
    assert responses["wrong"].json() == {"detail": CODE_REFUSED}
    assert scalar(config, select(func.count()).select_from(User)) == 0


def test_a_used_up_code_refuses_the_next_person(
    client: TestClient, make_code: Callable[..., str]
) -> None:
    code = make_code(max_uses=1)
    assert signup(client, code, "first@example.com").status_code == 201

    assert signup(client, code, "second@example.com").json() == {"detail": CODE_REFUSED}


def test_existing_email_consumes_nothing(
    client: TestClient,
    config: WebConfig,
    make_code: Callable[..., str],
    add_user: Callable[..., str],
) -> None:
    add_user("taken@example.com")
    code = make_code(max_uses=1)

    response = signup(client, code, "Taken@Example.com")

    assert response.status_code == 400
    assert response.json() == {"detail": NOT_CREATED}
    assert scalar(config, select(InviteCode.uses)) == 0
    assert scalar(config, select(func.count()).select_from(InviteRedemption)) == 0
    assert signup(client, code, "fresh@example.com").status_code == 201  # still usable


def test_missing_code_is_refused_when_invites_are_required(client: TestClient) -> None:
    assert signup(client, None).json() == {"detail": CODE_REFUSED}


def test_no_code_needed_when_the_gate_is_off(config: WebConfig) -> None:
    open_config = replace(config, invite_required=False)
    with TestClient(
        create_app(open_config), base_url="https://testserver", headers={"origin": ORIGIN}
    ) as client:
        assert signup(client, None).status_code == 201


def test_the_user_cap_closes_sign_up(
    config: WebConfig, make_code: Callable[..., str], add_user: Callable[..., str]
) -> None:
    add_user("one@example.com")
    capped = replace(config, max_users=1)
    code = make_code()
    with TestClient(
        create_app(capped), base_url="https://testserver", headers={"origin": ORIGIN}
    ) as client:
        response = signup(client, code)

    assert response.status_code == 403
    assert response.json() == {"detail": CLOSED}
    assert scalar(config, select(InviteCode.uses)) == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"timezone": "Mars/Olympus_Mons"},
        {"timezone": ""},
        {"password": "short"},
        {"password": "p" * 129},
        {"email": "not-an-email"},
        {"is_superuser": True},
        {"is_active": False},
    ],
)
def test_invalid_input_is_refused_and_creates_nothing(
    client: TestClient, config: WebConfig, make_code: Callable[..., str], extra: dict[str, Any]
) -> None:
    response = signup(client, make_code(), **extra)

    assert response.status_code == 422
    assert scalar(config, select(func.count()).select_from(User)) == 0


def test_sign_up_is_limited_per_ip(client: TestClient) -> None:
    statuses = [signup(client, new_code(), f"u{i}@example.com").status_code for i in range(10)]
    assert statuses == [400] * 10  # precondition: under the limit the route answers

    response = signup(client, new_code(), "u10@example.com")

    assert response.status_code == 429
    assert "retry-after" in response.headers


@pytest.mark.anyio
async def test_racing_sign_ups_never_overrun_a_code(
    config: WebConfig, make_code: Callable[..., str]
) -> None:
    code = make_code(max_uses=3)
    engine = make_engine(config.database_url)
    sessions = make_session_factory(engine)
    results: list[str] = []

    async def attempt(i: int) -> None:
        async with sessions() as db:
            try:
                await create_account(
                    db,
                    config,
                    email=f"racer{i}@example.com",
                    hashed_password="x",
                    timezone="UTC",
                    code=code,
                )
                await db.commit()  # the caller owns the transaction
                results.append("ok")
            except SignupRefused:
                results.append("refused")

    async with anyio.create_task_group() as group:
        for i in range(20):
            group.start_soon(attempt, i)
    await engine.dispose()

    assert results.count("ok") == 3
    assert scalar(config, select(InviteCode.uses)) == 3
    assert scalar(config, select(func.count()).select_from(User)) == 3


def test_a_code_expiring_soon_by_the_database_clock_still_works(
    client: TestClient, make_code: Callable[..., str]
) -> None:
    code = make_code(expires_at=text("now() + interval '1 minute'"))

    assert signup(client, code).status_code == 201
