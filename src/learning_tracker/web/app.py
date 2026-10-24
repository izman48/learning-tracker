"""The web process: the JSON API and, later, the PWA's static files."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from learning_tracker.db.engine import make_engine, make_session_factory
from learning_tracker.web.auth.routes import router as auth_router
from learning_tracker.web.config import WebConfig
from learning_tracker.web.security import SecurityMiddleware, security_headers


async def validation_error(_request: Request, exc: Exception) -> JSONResponse:
    """422 without echoing what was submitted.

    FastAPI's default body includes each error's `input` (and `ctx`), which would
    send a rejected password back to the client and into any proxy or client log.
    """
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    detail = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in errors]
    return JSONResponse({"detail": detail}, status_code=422)


def server_error(public_origin: str) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """500 with the security headers and no detail; the exception is still logged."""

    async def handler(request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            {"detail": "Something went wrong"},
            status_code=500,
            headers=security_headers(public_origin, request.url.path),
        )

    return handler


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
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, server_error(config.public_origin))
    app.add_middleware(SecurityMiddleware, public_origin=config.public_origin)
    app.include_router(auth_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
