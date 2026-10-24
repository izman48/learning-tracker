"""Sign-in brute force is slowed per IP and per account, and every password re-entry
shares the per-account bucket. Limits answer 429 with Retry-After."""

import logging
from collections.abc import Callable, Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from learning_tracker.db.models import RateLimitBucket
from learning_tracker.web.app import create_app
from learning_tracker.web.config import WebConfig
from tests.web.conftest import ORIGIN

PASSWORD = "correct horse battery"
AddUser = Callable[..., str]


@pytest.fixture
def from_ip(config: WebConfig) -> Iterator[Callable[[str], TestClient]]:
    clients: list[TestClient] = []

    def make(ip: str) -> TestClient:
        client = TestClient(
            create_app(config),
            base_url="https://testserver",
            headers={"origin": ORIGIN},
            client=(ip, 50000),
        )
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def login(client: TestClient, email: str, password: str = "wrong password") -> int:
    return client.post("/api/auth/login", json={"email": email, "password": password}).status_code


def test_per_ip_limit_is_5_a_minute_with_retry_after(
    from_ip: Callable[[str], TestClient], add_user: AddUser
) -> None:
    add_user()
    client = from_ip("203.0.113.5")
    statuses = [login(client, f"user{i}@example.com") for i in range(5)]
    assert statuses == [400] * 5  # precondition: under the limit the route answers

    response = client.post(
        "/api/auth/login", json={"email": "reader@example.com", "password": PASSWORD}
    )

    assert response.status_code == 429
    assert 0 < int(response.headers["retry-after"]) <= 60
    assert response.json() == {"detail": "Too many tries. Wait a few minutes, then try again."}


def test_another_ip_has_its_own_budget(from_ip: Callable[[str], TestClient]) -> None:
    first = from_ip("203.0.113.5")
    for i in range(5):
        login(first, f"user{i}@example.com")
    assert login(first, "x@example.com") == 429  # precondition

    assert login(from_ip("198.51.100.7"), "x@example.com") == 400


def test_per_account_limit_spans_ips(
    from_ip: Callable[[str], TestClient], add_user: AddUser
) -> None:
    add_user()
    # 10 tries for one account from 5 IPs (2 each, under the per-IP limit).
    for i in range(10):
        assert login(from_ip(f"203.0.113.{i // 2 + 1}"), "reader@example.com") == 400

    fresh_ip = from_ip("198.51.100.9")
    assert login(fresh_ip, "reader@example.com", PASSWORD) == 429  # even the right password
    assert login(fresh_ip, "other@example.com") == 400  # another account is unaffected


def test_account_bucket_ignores_email_case(
    from_ip: Callable[[str], TestClient], add_user: AddUser
) -> None:
    add_user()
    for i in range(10):
        login(
            from_ip(f"203.0.113.{i // 2 + 1}"),
            "Reader@Example.com" if i % 2 else "reader@example.com",
        )

    assert login(from_ip("198.51.100.9"), "READER@example.com", PASSWORD) == 429


def test_password_change_shares_the_account_bucket(
    from_ip: Callable[[str], TestClient], add_user: AddUser
) -> None:
    add_user()
    me = from_ip("203.0.113.1")
    assert login(me, "reader@example.com", PASSWORD) == 204
    # 9 more failed sign-ins for this account from elsewhere: 10 attempts in all.
    for i in range(9):
        login(from_ip(f"198.51.100.{i // 2 + 1}"), "reader@example.com")

    response = me.post(
        "/api/me/password",
        json={"current_password": "a guess", "new_password": "a brand new passphrase"},
    )

    assert response.status_code == 429
    assert "retry-after" in response.headers


def test_buckets_store_keyed_hashes_not_ips_or_emails(
    from_ip: Callable[[str], TestClient], config: WebConfig
) -> None:
    login(from_ip("203.0.113.5"), "reader@example.com")

    engine = create_engine(config.database_url)
    with engine.connect() as conn:
        keys = conn.execute(select(RateLimitBucket.key)).scalars().all()
    engine.dispose()
    assert len(keys) == 2  # precondition: an IP bucket and an account bucket
    assert not any("203.0.113.5" in k or "reader" in k or "example" in k for k in keys)


def test_private_caller_without_a_trusted_proxy_warns_once(
    from_ip: Callable[[str], TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    # Behind an undeclared reverse proxy every caller has the proxy's private address,
    # so one per-IP budget would cover the whole instance.
    caplog.set_level(logging.WARNING)
    client = from_ip("172.18.0.1")

    login(client, "a@example.com")
    login(client, "b@example.com")

    warnings = [r for r in caplog.records if "LT_TRUSTED_PROXY_IPS" in r.getMessage()]
    assert len(warnings) == 1
    assert client.app.state.proxy_warning is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("ip", ["203.0.113.5", "127.0.0.1"])
def test_public_or_loopback_callers_do_not_warn(
    from_ip: Callable[[str], TestClient], caplog: pytest.LogCaptureFixture, ip: str
) -> None:
    caplog.set_level(logging.WARNING)

    login(from_ip(ip), "a@example.com")

    assert not [r for r in caplog.records if "LT_TRUSTED_PROXY_IPS" in r.getMessage()]


def test_no_warning_when_a_trusted_proxy_is_declared(
    config: WebConfig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    declared = replace(config, trusted_proxy_ips=("172.18.0.1",))
    with TestClient(
        create_app(declared),
        base_url="https://testserver",
        headers={"origin": ORIGIN},
        client=("172.18.0.1", 50000),
    ) as client:
        login(client, "a@example.com")

    assert not [r for r in caplog.records if "LT_TRUSTED_PROXY_IPS" in r.getMessage()]


def test_ipv6_callers_share_a_budget_per_64(from_ip: Callable[[str], TestClient]) -> None:
    # One host can hold a whole /64; rotating through it must not reset the budget.
    for i in range(5):
        assert login(from_ip(f"2001:db8:1:2::{i + 1:x}"), f"u{i}@example.com") == 400

    assert login(from_ip("2001:db8:1:2:ffff::9"), "x@example.com") == 429  # same /64
    assert login(from_ip("2001:db8:1:3::1"), "x@example.com") == 400  # next /64


def test_ipv4_mapped_ipv6_counts_as_the_ipv4_address(
    from_ip: Callable[[str], TestClient],
) -> None:
    for i in range(5):
        login(from_ip("203.0.113.5"), f"u{i}@example.com")

    assert login(from_ip("::ffff:203.0.113.5"), "x@example.com") == 429


def test_account_bucket_follows_the_user_not_the_typed_email(
    from_ip: Callable[[str], TestClient], add_user: AddUser
) -> None:
    add_user()
    # Unicode case forms that Python's lower() leaves apart still reach one account
    # bucket, because a found user is keyed by id.
    variants = ["reader@example.com", "READER@EXAMPLE.COM", "Reader@Example.com"]
    for i in range(10):
        login(from_ip(f"203.0.113.{i // 2 + 1}"), variants[i % 3])

    assert login(from_ip("198.51.100.9"), "reader@example.com", PASSWORD) == 429


def test_unknown_emails_are_bucketed_by_a_normalised_form(
    from_ip: Callable[[str], TestClient],
) -> None:
    for i in range(10):
        login(
            from_ip(f"203.0.113.{i // 2 + 1}"),
            "ghost@example.com" if i % 2 else " GHOST@example.com",
        )

    assert login(from_ip("198.51.100.9"), "ghost@EXAMPLE.com") == 429


def test_password_hashing_fails_fast_when_every_slot_is_busy(
    from_ip: Callable[[str], TestClient], add_user: AddUser
) -> None:
    add_user()
    client = from_ip("203.0.113.5")
    client.app.state.password_slots.capacity = 0  # type: ignore[attr-defined]

    response = client.post(
        "/api/auth/login", json={"email": "reader@example.com", "password": PASSWORD}
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    client.app.state.password_slots.capacity = 1  # type: ignore[attr-defined]
    assert login(client, "reader@example.com", PASSWORD) == 204  # precondition: slots work
