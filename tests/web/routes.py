"""Walk every API route of an app, including ones inside included routers.

FastAPI wraps included routers in a private route type, so app.routes alone misses
them. Duck typing on `original_router` avoids importing FastAPI internals.
"""

from collections.abc import Iterable, Iterator
from typing import Any

from fastapi.routing import APIRoute


def api_routes(routes: Iterable[Any]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from api_routes(inner.routes)
