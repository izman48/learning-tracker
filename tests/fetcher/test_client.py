"""The session-level guard: validate_url on the first request and every redirect hop.

These tests use a plain, unvetted aiohttp connector on purpose, to prove this layer
holds on its own. The connector's socket check is proven separately in test_resolver.
"""

from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from yarl import URL

from learning_tracker.fetcher.client import UrlCheck, guarded_session
from learning_tracker.fetcher.urls import TargetURL, UnsafeURL, validate_url

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def server() -> AsyncIterator[tuple[int, list[str]]]:
    hits: list[str] = []

    async def page(request: web.Request) -> web.Response:
        hits.append(request.path)
        return web.Response(text="ok")

    async def to_metadata(request: web.Request) -> web.Response:
        hits.append(request.path)
        raise web.HTTPFound("http://169.254.169.254/latest/meta-data/")

    async def to_relative(request: web.Request) -> web.Response:
        hits.append(request.path)
        raise web.HTTPFound("/page")

    app = web.Application()
    app.router.add_get("/page", page)
    app.router.add_get("/to-metadata", to_metadata)
    app.router.add_get("/to-relative", to_relative)
    srv = TestServer(app, host="127.0.0.1")
    await srv.start_server()
    assert srv.port is not None
    yield srv.port, hits
    await srv.close()


def harness_allowed(port: int) -> tuple[list[str], UrlCheck]:
    """validate_url, except that the local harness origin stands in for a public site."""
    calls: list[str] = []
    harness = f"http://127.0.0.1:{port}/"

    def check(raw: str, *, base: str | None = None) -> TargetURL:
        joined = str(URL(base).join(URL(raw))) if base else raw
        calls.append(joined)
        return validate_url("https://example.com/" if joined.startswith(harness) else joined)

    return calls, check


async def test_real_validate_url_refuses_an_ip_literal_before_any_connection(
    server: tuple[int, list[str]],
) -> None:
    port, hits = server
    async with guarded_session(connector=aiohttp.TCPConnector()) as session:
        with pytest.raises(UnsafeURL, match="ip literal"):
            await session.get(f"http://127.0.0.1:{port}/page")
    assert hits == []


async def test_control_passes_and_every_redirect_hop_is_checked(
    server: tuple[int, list[str]],
) -> None:
    port, hits = server
    calls, check = harness_allowed(port)
    async with (
        guarded_session(connector=aiohttp.TCPConnector(), check_url=check) as session,
        session.get(f"http://127.0.0.1:{port}/to-relative") as resp,
    ):
        assert resp.status == 200
    assert hits == ["/to-relative", "/page"]
    assert calls == [
        f"http://127.0.0.1:{port}/to-relative",
        f"http://127.0.0.1:{port}/page",
    ]


async def test_a_redirect_to_metadata_is_refused_before_it_is_followed(
    server: tuple[int, list[str]],
) -> None:
    port, hits = server
    calls, check = harness_allowed(port)
    timeout = aiohttp.ClientTimeout(total=3)
    async with guarded_session(
        connector=aiohttp.TCPConnector(), check_url=check, timeout=timeout
    ) as session:
        with pytest.raises(UnsafeURL, match="ip literal"):
            await session.get(f"http://127.0.0.1:{port}/to-metadata")
    assert hits == ["/to-metadata"]
    assert calls[-1] == "http://169.254.169.254/latest/meta-data/"
