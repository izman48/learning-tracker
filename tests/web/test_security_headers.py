"""Every response carries the security headers, with the exact CSP the ADR fixes."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from learning_tracker.web.app import create_app
from learning_tracker.web.config import WebConfig

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'; manifest-src 'self'; worker-src 'self'"
)
EXPECTED = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "accelerometer=(), gyroscope=(), magnetometer=(), display-capture=()"
    ),
    "strict-transport-security": "max-age=63072000",
}


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", "/healthz", {}),
        ("GET", "/api/me", {}),  # 401
        ("GET", "/no/such/page", {}),  # 404
        ("POST", "/api/auth/login", {"json": {"email": "a@example.com", "password": "x"}}),
        ("POST", "/api/auth/login", {"headers": {"origin": "https://evil.example.org"}}),  # 403
    ],
)
def test_headers_on_every_kind_of_response(
    client: TestClient, method: str, path: str, kwargs: dict[str, object]
) -> None:
    response = client.request(method, path, **kwargs)  # type: ignore[arg-type]

    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value, name


def test_api_responses_are_not_cached(client: TestClient) -> None:
    assert client.get("/api/me").headers["cache-control"] == "no-store"


def test_no_hsts_for_a_plain_http_localhost_origin(config: WebConfig) -> None:
    local = replace(config, public_origin="http://localhost:8080")
    response = TestClient(create_app(local)).get("/healthz")

    assert response.headers["x-content-type-options"] == "nosniff"  # precondition
    assert "strict-transport-security" not in response.headers


def test_unhandled_errors_still_carry_the_headers_and_no_traceback(config: WebConfig) -> None:
    app = create_app(config)

    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    response = TestClient(app, raise_server_exceptions=False).get("/api/boom")

    assert response.status_code == 500
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value, name
    assert response.headers["cache-control"] == "no-store"
    assert "internal detail" not in response.text and "Traceback" not in response.text
