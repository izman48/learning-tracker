"""Limits a hostile or slow server cannot get round: one deadline for the whole fetch,
a cap on decompressed bytes, no cookies or proxy settings, and invalid TLS as unsafe."""

import asyncio
import gzip
import ssl
import time
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web
from aiohttp.client_reqrep import ConnectionKey
from aiohttp.test_utils import TestServer

from learning_tracker.fetcher import fetch as fetch_module
from learning_tracker.fetcher.client import fetcher_session
from learning_tracker.fetcher.fetch import (
    MAX_BODY_BYTES,
    Outcome,
    classify_client_error,
    fetch_page,
)
from tests.fetcher.harness import FakeDNS, only_the_harness_is_public

pytestmark = pytest.mark.anyio
SEEN: dict[str, dict[str, str]] = {}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def drip(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(headers={"Content-Type": "text/html"})
    await resp.prepare(request)
    for _ in range(60):  # one byte a second for a minute
        await resp.write(b"x")
        await asyncio.sleep(1)
    return resp


async def stall(_request: web.Request) -> web.Response:
    await asyncio.sleep(60)
    return web.Response(text="late", content_type="text/html")


async def big(request: web.Request) -> web.Response:
    size = int(request.query["bytes"])
    return web.Response(body=b"a" * size, content_type="text/html")


async def bomb(_request: web.Request) -> web.Response:
    packed = gzip.compress(b"\0" * (MAX_BODY_BYTES + 1024 * 1024))  # ~6 MB, a few KB packed
    return web.Response(
        body=packed, headers={"Content-Type": "text/html", "Content-Encoding": "gzip"}
    )


async def set_cookie(_request: web.Request) -> web.Response:
    resp = web.HTTPFound("/echo")
    resp.set_cookie("tracker", "abc")
    raise resp


async def echo(request: web.Request) -> web.Response:
    SEEN["echo"] = dict(request.headers)
    return web.Response(text="<html>ok</html>", content_type="text/html")


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    app = web.Application()
    for path, handler in [("/drip", drip), ("/stall", stall), ("/big", big), ("/bomb", bomb),
                          ("/set-cookie", set_cookie), ("/echo", echo)]:  # fmt: skip
        app.router.add_get(path, handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    dns = FakeDNS({"site.example": ["127.0.0.1"]}, port=server.port)
    s = fetcher_session(
        "https://example.com/contact", resolver=dns,
        is_allowed=only_the_harness_is_public("127.0.0.1"),
    )  # fmt: skip
    yield s
    await s.close()
    await server.close()


@pytest.mark.parametrize("path", ["/drip", "/stall"])
async def test_one_deadline_covers_the_whole_fetch(
    session: aiohttp.ClientSession, path: str
) -> None:
    started = time.monotonic()
    r = await fetch_page(session, f"http://site.example{path}", deadline=1.0)
    assert r.outcome == Outcome.TIMEOUT
    assert time.monotonic() - started < 2.5  # a slow drip cannot hold the slot open


def test_the_production_deadline_is_15_seconds() -> None:
    assert fetch_module.FETCH_DEADLINE_S == 15 and MAX_BODY_BYTES == 5 * 1024 * 1024


async def test_a_body_at_the_cap_is_read(session: aiohttp.ClientSession) -> None:
    r = await fetch_page(session, f"http://site.example/big?bytes={MAX_BODY_BYTES}")
    assert r.outcome == Outcome.OK and r.body is not None and len(r.body) == MAX_BODY_BYTES


async def test_a_body_over_the_cap_is_abandoned(session: aiohttp.ClientSession) -> None:
    r = await fetch_page(session, f"http://site.example/big?bytes={MAX_BODY_BYTES + 1}")
    assert (r.outcome, r.reason, r.body) == (Outcome.NOT_ARTICLE, "too large", None)


async def test_a_compression_bomb_is_stopped_by_decompressed_size(
    session: aiohttp.ClientSession,
) -> None:
    r = await fetch_page(session, "http://site.example/bomb")
    assert (r.outcome, r.reason) == (Outcome.NOT_ARTICLE, "too large")


async def test_no_cookie_is_ever_sent_back(session: aiohttp.ClientSession) -> None:
    r = await fetch_page(session, "http://site.example/set-cookie")
    assert r.outcome == Outcome.OK and r.final_url == "http://site.example/echo"  # precondition
    assert "Cookie" not in SEEN["echo"]


async def test_requests_carry_an_honest_user_agent_and_no_credentials(
    session: aiohttp.ClientSession,
) -> None:
    await fetch_page(session, "http://site.example/echo")
    headers = SEEN["echo"]
    assert headers["User-Agent"] == (
        "Mozilla/5.0 (compatible; learning-tracker; +https://example.com/contact)"
    )
    assert headers["Accept-Encoding"] == "gzip, deflate"
    assert "Authorization" not in headers and "Cookie" not in headers


async def test_proxy_settings_in_the_environment_are_ignored(
    session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://proxy.invalid:3128")
    r = await fetch_page(session, "http://site.example/echo")
    assert r.outcome == Outcome.OK


def test_invalid_tls_is_unsafe_not_dead() -> None:
    key = ConnectionKey("site.example", 443, True, True, None, None, None)
    cert_error = aiohttp.ClientConnectorCertificateError(key, ssl.SSLCertVerificationError())
    assert classify_client_error(cert_error) == (Outcome.UNSAFE, "invalid tls")
    ssl_error = aiohttp.ClientConnectorSSLError(key, ssl.SSLError())
    assert classify_client_error(ssl_error) == (Outcome.UNSAFE, "invalid tls")
    refused = aiohttp.ClientConnectorError(key, ConnectionRefusedError())
    assert classify_client_error(refused) == (Outcome.DEAD, "connection failed")  # control
