from fastapi.testclient import TestClient

from learning_tracker.web.app import create_app


def test_healthz_reports_ok_and_nothing_else() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_does_not_leak_server_details_in_headers() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert "server" not in {name.lower() for name in response.headers}
    assert "x-powered-by" not in {name.lower() for name in response.headers}


def test_api_docs_are_not_published() -> None:
    client = TestClient(create_app())

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
