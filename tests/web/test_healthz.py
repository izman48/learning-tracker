from datetime import timedelta

from fastapi.testclient import TestClient

from learning_tracker.web.app import create_app
from learning_tracker.web.config import WebConfig

# No database is touched: without the context manager TestClient skips the lifespan.
CONFIG = WebConfig(
    database_url="postgresql+psycopg://unused@localhost/unused",
    session_key=b"k" * 32,
    session_lifetime=timedelta(days=1),
    public_origin="https://reads.example.com",
    ip_key=b"i" * 32,
)


def test_healthz_reports_ok_and_nothing_else() -> None:
    client = TestClient(create_app(CONFIG))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_does_not_leak_server_details_in_headers() -> None:
    client = TestClient(create_app(CONFIG))

    response = client.get("/healthz")

    assert "server" not in {name.lower() for name in response.headers}
    assert "x-powered-by" not in {name.lower() for name in response.headers}


def test_api_docs_are_not_published() -> None:
    client = TestClient(create_app(CONFIG))

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
