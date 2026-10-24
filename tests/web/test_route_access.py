"""Every route needs a signed-in user unless it is on the explicit public list.

A new route without the current_user dependency fails here until someone adds it to
PUBLIC on purpose (ADR D-18)."""

from collections.abc import Callable, Iterator
from typing import Any

from fastapi.dependencies.models import Dependant

from learning_tracker.web.admin.deps import current_admin
from learning_tracker.web.app import create_app
from learning_tracker.web.auth.deps import current_user
from learning_tracker.web.config import WebConfig
from tests.web.routes import api_routes

PUBLIC = {
    ("GET", "/healthz"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/reset"),  # the reset token is the credential
    ("POST", "/api/auth/logout"),  # works without a session: just clears the cookie
}


def calls(dependant: Dependant) -> Iterator[Callable[..., Any] | None]:
    yield dependant.call
    for sub in dependant.dependencies:
        yield from calls(sub)


def unprotected(app_routes: Any) -> set[tuple[str, str]]:
    found = set()
    for route in api_routes(app_routes):
        if not {current_user, current_admin} & set(calls(route.dependant)):
            found |= {(method, route.path) for method in route.methods or set()}
    return found


def test_every_non_public_route_requires_sign_in(config: WebConfig) -> None:
    app = create_app(config)
    routes = {(m, r.path) for r in api_routes(app.routes) for m in r.methods or set()}
    assert ("GET", "/api/me") in routes  # precondition: protected routes are walked

    assert unprotected(app.routes) - PUBLIC == set()


def test_public_list_has_no_stale_entries(config: WebConfig) -> None:
    routes = {
        (m, r.path) for r in api_routes(create_app(config).routes) for m in r.methods or set()
    }

    assert routes >= PUBLIC


def test_detector_fires_on_a_route_without_sign_in(config: WebConfig) -> None:
    app = create_app(config)

    @app.get("/api/leaky")
    def leaky() -> dict[str, str]:
        return {}

    assert ("GET", "/api/leaky") in unprotected(app.routes)
