"""The web process: the JSON API and, later, the PWA's static files."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from learning_tracker.db.engine import make_engine, make_session_factory
from learning_tracker.web.config import WebConfig


def create_app(config: WebConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(config.database_url)
        app.state.session_factory = make_session_factory(engine)
        yield
        await engine.dispose()

    # No published API docs: the API is for our own PWA, not a public surface.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.config = config

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
