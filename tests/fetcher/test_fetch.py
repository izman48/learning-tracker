"""The fetch loop: manual redirects with every hop re-checked, outcome classes, and
the content-type gate. Every page lives on a local server that fake DNS maps to,
so test URLs keep port 80 and pass the real URL checks unchanged."""

import socket
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from learning_tracker.fetcher.client import fetcher_session
from learning_tracker.fetcher.fetch import Outcome, fetch_page
from tests.fetcher.harness import FakeDNS, only_the_harness_is_public

pytestmark = pytest.mark.anyio
HTML = "<!doctype html><html><head><title>A page</title></head><body>words</body></html>"
SITES = ["site.example", "www.site.example", "other.example", "closed.example"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def build_app(hits: list[str]) -> web.Application:
    async def handle(request: web.Request) -> web.StreamResponse:
        hits.append(f"{request.host.split(':')[0]}{request.path}")
        path = request.path
        if path == "/page":
            return web.Response(text=HTML, content_type="text/html")
        if path == "/xhtml":
            return web.Response(text=HTML, content_type="application/xhtml+xml")
        if path.startswith("/go/"):  # /go/<host>/<path...>: redirect elsewhere
            target = path.removeprefix("/go/")
            raise web.HTTPFound(f"http://{target}")
        if path == "/relative":
            raise web.HTTPMovedPermanently("/page")
        if path.startswith("/chain/"):
            n = int(path.removeprefix("/chain/"))
            raise web.HTTPFound("/page" if n == 0 else f"/chain/{n - 1}")
        if path == "/no-location":
            return web.Response(status=302)
        if path.startswith("/status/"):
            return web.Response(status=int(path.removeprefix("/status/")), text="x")
        if path == "/retry":
            return web.Response(status=429, headers={"Retry-After": "120"})
        if path.startswith("/challenge/"):
            code = int(path.removeprefix("/challenge/"))
            return web.Response(status=code, text=HTML, content_type="text/html",
                                headers={"cf-mitigated": "challenge"})  # fmt: skip
        if path == "/pdf":
            return web.Response(body=b"%PDF-1.7 ...", content_type="application/pdf")
        if path == "/png":
            return web.Response(body=b"\x89PNG", content_type="image/png")
        raise web.HTTPNotFound()

    app = web.Application()
    app.router.add_route("GET", "/{tail:.*}", handle)
    return app


@pytest.fixture
async def site() -> AsyncIterator[tuple[aiohttp.ClientSession, list[str]]]:
    hits: list[str] = []
    server = TestServer(build_app(hits), host="127.0.0.1")
    await server.start_server()
    assert server.port is not None
    dns = FakeDNS({name: ["127.0.0.1"] for name in SITES}, port=server.port)
    session = fetcher_session(
        "https://example.com/contact", resolver=dns,
        is_allowed=only_the_harness_is_public("127.0.0.1"),
    )  # fmt: skip
    yield session, hits
    await session.close()
    await server.close()


async def test_an_html_page_is_ok_with_its_body(
    site: tuple[aiohttp.ClientSession, list[str]],
) -> None:
    session, _ = site
    r = await fetch_page(session, "http://site.example/page")
    assert (r.outcome, r.status, r.final_url) == (Outcome.OK, 200, "http://site.example/page")
    assert r.content_type == "text/html" and r.body == HTML.encode()


async def test_xhtml_is_an_article_type(site: tuple[aiohttp.ClientSession, list[str]]) -> None:
    session, _ = site
    assert (await fetch_page(session, "http://site.example/xhtml")).outcome == Outcome.OK


@pytest.mark.parametrize(
    "path,final",
    [
        ("/relative", "http://site.example/page"),
        ("/go/www.site.example/page", "http://www.site.example/page"),
        ("/chain/4", "http://site.example/page"),  # 5 redirects is the limit
    ],
)
async def test_same_site_redirects_are_followed(
    site: tuple[aiohttp.ClientSession, list[str]], path: str, final: str
) -> None:
    session, _ = site
    r = await fetch_page(session, f"http://site.example{path}")
    assert (r.outcome, r.final_url) == (Outcome.OK, final)


async def test_a_sixth_redirect_is_dead(site: tuple[aiohttp.ClientSession, list[str]]) -> None:
    session, hits = site
    r = await fetch_page(session, "http://site.example/chain/5")
    assert (r.outcome, r.reason) == (Outcome.DEAD, "too many redirects")
    assert len(hits) == 6 and "site.example/page" not in hits


@pytest.mark.parametrize(
    "target,reason",
    [
        ("other.example/page", "redirect to another site"),
        ("169.254.169.254/latest/meta-data/", "ip literal host"),
        ("127.0.0.1/page", "ip literal host"),
        ("[::1]/page", "ip literal host"),
        ("site.example:8080/page", "port not allowed"),
    ],
)
async def test_unsafe_redirects_are_refused_before_they_are_followed(
    site: tuple[aiohttp.ClientSession, list[str]], target: str, reason: str
) -> None:
    session, hits = site
    r = await fetch_page(session, f"http://site.example/go/{target}")
    assert (r.outcome, r.reason) == (Outcome.UNSAFE, reason)
    assert hits == [f"site.example/go/{target}"]  # the next hop was never requested


async def test_the_first_url_is_checked_too(site: tuple[aiohttp.ClientSession, list[str]]) -> None:
    session, hits = site
    for url in ("http://169.254.169.254/", "ftp://site.example/", "http://site.example:22/"):
        assert (await fetch_page(session, url)).outcome == Outcome.UNSAFE
    assert hits == []


async def test_a_redirect_without_location_is_dead(
    site: tuple[aiohttp.ClientSession, list[str]],
) -> None:
    session, _ = site
    assert (await fetch_page(session, "http://site.example/no-location")).outcome == Outcome.DEAD


@pytest.mark.parametrize(
    "code,outcome",
    [
        (401, Outcome.WALLED),
        (451, Outcome.WALLED),
        (403, Outcome.BLOCKED),
        (429, Outcome.BLOCKED),
        (503, Outcome.BLOCKED),
        (500, Outcome.BLOCKED),  # transient server trouble: unverified, retried later
        (404, Outcome.DEAD),
        (410, Outcome.DEAD),
        (400, Outcome.DEAD),
    ],
)
async def test_status_codes_map_to_outcome_classes(
    site: tuple[aiohttp.ClientSession, list[str]], code: int, outcome: Outcome
) -> None:
    session, _ = site
    r = await fetch_page(session, f"http://site.example/status/{code}")
    assert (r.outcome, r.status, r.body) == (outcome, code, None)


async def test_retry_after_is_kept_for_the_politeness_layer(
    site: tuple[aiohttp.ClientSession, list[str]],
) -> None:
    session, _ = site
    r = await fetch_page(session, "http://site.example/retry")
    assert (r.outcome, r.retry_after) == (Outcome.BLOCKED, "120")


@pytest.mark.parametrize("code", [200, 403])
async def test_a_bot_challenge_page_is_blocked_even_with_200(
    site: tuple[aiohttp.ClientSession, list[str]], code: int
) -> None:
    session, _ = site
    r = await fetch_page(session, f"http://site.example/challenge/{code}")
    assert (r.outcome, r.reason, r.body) == (Outcome.BLOCKED, "bot challenge", None)


@pytest.mark.parametrize("path,reason", [("/pdf", "pdf"), ("/png", "not html (image/png)")])
async def test_non_html_is_not_an_article_and_its_body_is_not_read(
    site: tuple[aiohttp.ClientSession, list[str]], path: str, reason: str
) -> None:
    session, _ = site
    r = await fetch_page(session, f"http://site.example{path}")
    assert (r.outcome, r.reason, r.body) == (Outcome.NOT_ARTICLE, reason, None)


async def test_unknown_host_is_dead_not_unsafe(
    site: tuple[aiohttp.ClientSession, list[str]],
) -> None:
    session, _ = site
    r = await fetch_page(session, "http://nxdomain.example/page")
    assert (r.outcome, r.reason) == (Outcome.DEAD, "dns")


async def test_connection_refused_is_dead() -> None:
    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    dns = FakeDNS({"closed.example": ["127.0.0.1"]}, port=closed_port)
    policy = only_the_harness_is_public("127.0.0.1")
    async with fetcher_session("https://example.com/c", resolver=dns, is_allowed=policy) as session:
        r = await fetch_page(session, "http://closed.example/page")
    assert (r.outcome, r.reason) == (Outcome.DEAD, "connection failed")


async def test_a_host_resolving_to_metadata_is_unsafe() -> None:
    dns = FakeDNS({"meta.example": ["169.254.169.254"]})
    async with fetcher_session("https://example.com/c", resolver=dns) as session:
        r = await fetch_page(session, "http://meta.example/")
    assert (r.outcome, r.reason) == (Outcome.UNSAFE, "non-public address")
