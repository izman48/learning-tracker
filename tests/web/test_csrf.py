"""CSRF: a state-changing request must come from our own origin, as JSON.

SameSite=Lax already keeps the cookie off most cross-site POSTs; this is the
second layer, and it does not depend on the browser's cookie rules."""

import pytest
from fastapi.testclient import TestClient

from tests.web.conftest import ORIGIN

LOGIN = {"email": "reader@example.com", "password": "correct horse battery"}


def post(client: TestClient, headers: dict[str, str], **kw: object) -> int:
    return client.post("/api/auth/login", headers=headers, **kw).status_code  # type: ignore[arg-type]


def test_our_own_origin_with_json_gets_through(client: TestClient) -> None:
    # 400 = the request reached the route (bad credentials), so CSRF let it pass.
    assert post(client, {"origin": ORIGIN}, json=LOGIN) == 400


def test_missing_origin_is_refused(client: TestClient) -> None:
    client.headers.pop("origin")

    assert post(client, {}, json=LOGIN) == 403


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example.org",
        "http://reads.example.com",  # scheme differs
        "https://reads.example.com:8443",  # port differs
        "https://reads.example.com.evil.example.org",
        "null",
    ],
)
def test_foreign_origin_is_refused(client: TestClient, origin: str) -> None:
    assert post(client, {"origin": origin}, json=LOGIN) == 403


def test_cross_site_fetch_metadata_is_refused_even_with_our_origin(client: TestClient) -> None:
    assert post(client, {"origin": ORIGIN, "sec-fetch-site": "cross-site"}, json=LOGIN) == 403
    assert post(client, {"origin": ORIGIN, "sec-fetch-site": "same-origin"}, json=LOGIN) == 400


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"],
)
def test_non_json_body_is_refused(client: TestClient, content_type: str) -> None:
    response = client.post(
        "/api/auth/login",
        headers={"origin": ORIGIN, "content-type": content_type},
        content=b'{"email":"reader@example.com","password":"x"}',
    )

    assert response.status_code == 415


def test_json_with_a_charset_is_accepted(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        headers={"origin": ORIGIN, "content-type": "application/json; charset=utf-8"},
        content=b'{"email":"reader@example.com","password":"wrong one"}',
    )

    assert response.status_code == 400


def test_safe_methods_need_no_origin(client: TestClient) -> None:
    client.headers.pop("origin")

    assert client.get("/healthz").status_code == 200
