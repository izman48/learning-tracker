"""The web process: the JSON API and, later, the PWA's static files."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    # No published API docs: the API is for our own PWA, not a public surface.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
