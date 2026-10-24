"""Politeness: robots.txt through the same guarded client, Retry-After per host, at
most three attempts per URL, and the connector's concurrency limits."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from learning_tracker.fetcher.client import fetcher_session
from learning_tracker.fetcher.fetch import Outcome
from learning_tracker.fetcher.polite import MAX_RETRY_AFTER_S, ROBOTS_TTL_S, PoliteFetcher
from tests.fetcher.harness import FakeDNS, only_the_harness_is_public

pytestmark = pytest.mark.anyio
HTML = "<html><title>t</title><body>words</body></html>"
SITE: web.AppKey[Site] = web.AppKey("site")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Site:
    """What the test server answers, per host, and what it saw."""

    robots: dict[str, tuple[int, str]] = field(default_factory=dict)
    hits: list[str] = field(default_factory=list)
    in_flight: dict[str, int] = field(default_factory=dict)
    peak: dict[str, int] = field(default_factory=dict)
    total_peak: int = 0


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


async def handle(request: web.Request) -> web.StreamResponse:
    site = request.app[SITE]
    host = request.host.split(":")[0]
    path = request.path
    site.hits.append(f"{host}{path}")
    if path == "/robots.txt":
        if host == "stall.example":
            await asyncio.sleep(30)
        if host == "jump.example":
            raise web.HTTPFound("http://169.254.169.254/robots.txt")
        status, body = site.robots.get(host, (404, ""))
        return web.Response(status=status, text=body, content_type="text/plain")
    if path == "/slow":
        site.in_flight[host] = site.in_flight.get(host, 0) + 1
        site.peak[host] = max(site.peak.get(host, 0), site.in_flight[host])
        site.total_peak = max(site.total_peak, sum(site.in_flight.values()))
        await asyncio.sleep(0.2)
        site.in_flight[host] -= 1
        return web.Response(text=HTML, content_type="text/html")
    if path == "/busy":
        return web.Response(status=429, headers={"Retry-After": request.query["after"]})
    if path == "/down":
        return web.Response(status=503, headers={"Retry-After": "60"})
    if path == "/broken":
        return web.Response(status=500)
    return web.Response(text=HTML, content_type="text/html")


HOSTS = ["site.example", "stall.example", "jump.example"] + [f"h{i}.example" for i in range(6)]


@pytest.fixture
async def world() -> AsyncIterator[tuple[aiohttp.ClientSession, Site]]:
    site = Site()
    app = web.Application()
    app[SITE] = site
    app.router.add_route("GET", "/{tail:.*}", handle)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    dns = FakeDNS({h: ["127.0.0.1"] for h in HOSTS}, port=server.port)
    session = fetcher_session(
        "https://example.com/c", resolver=dns, is_allowed=only_the_harness_is_public("127.0.0.1")
    )
    yield session, site
    await session.close()
    await server.close()


def polite(session: aiohttp.ClientSession, clock: Clock | None = None) -> PoliteFetcher:
    return PoliteFetcher(session, clock=clock or Clock(), deadline=1.0)


# --- robots.txt ---


async def test_robots_disallow_stops_the_fetch_before_the_page_is_requested(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, site = world
    site.robots["site.example"] = (200, "User-agent: *\nDisallow: /private\n")
    f = polite(session)
    assert (await f.fetch("http://site.example/public")).outcome == Outcome.OK  # control
    r = await f.fetch("http://site.example/private")
    assert (r.outcome, r.reason) == (Outcome.BLOCKED, "robots.txt")
    assert "site.example/private" not in site.hits


async def test_our_own_user_agent_group_wins_over_the_wildcard(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, site = world
    site.robots["site.example"] = (
        200,
        "User-agent: *\nAllow: /\n\nUser-agent: learning-tracker\nDisallow: /\n",
    )
    r = await polite(session).fetch("http://site.example/page")
    assert (r.outcome, r.reason) == (Outcome.BLOCKED, "robots.txt")


async def test_a_missing_robots_file_allows_everything(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, _ = world
    assert (await polite(session).fetch("http://site.example/page")).outcome == Outcome.OK


@pytest.mark.parametrize("status", [500, 503])
async def test_a_robots_server_error_disallows_the_whole_host(
    world: tuple[aiohttp.ClientSession, Site], status: int
) -> None:
    session, site = world
    site.robots["site.example"] = (status, "")
    r = await polite(session).fetch("http://site.example/page")
    assert (r.outcome, r.reason) == (Outcome.BLOCKED, "robots.txt")
    assert site.hits == ["site.example/robots.txt"]


async def test_a_robots_timeout_disallows_the_whole_host(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, site = world
    r = await polite(session).fetch("http://stall.example/page")
    assert (r.outcome, r.reason) == (Outcome.BLOCKED, "robots.txt")
    assert "stall.example/page" not in site.hits


async def test_a_robots_redirect_to_metadata_is_refused_and_disallows_the_host(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, site = world
    r = await polite(session).fetch("http://jump.example/page")
    assert (r.outcome, r.reason) == (Outcome.BLOCKED, "robots.txt")
    assert site.hits == ["jump.example/robots.txt"]  # neither the page nor metadata


async def test_robots_is_fetched_once_per_host_per_day(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, site = world
    clock = Clock()
    f = polite(session, clock)
    await f.fetch("http://site.example/a")
    await f.fetch("http://site.example/b")
    assert site.hits.count("site.example/robots.txt") == 1
    clock.now += ROBOTS_TTL_S + 1
    await f.fetch("http://site.example/c")
    assert site.hits.count("site.example/robots.txt") == 2


# --- Retry-After ---


async def test_retry_after_seconds_holds_the_host_until_then(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, site = world
    clock = Clock()
    f = polite(session, clock)
    first = await f.fetch("http://site.example/busy?after=120")
    assert (first.outcome, first.status) == (Outcome.BLOCKED, 429)
    held = await f.fetch("http://site.example/page")
    assert (held.outcome, held.reason) == (Outcome.BLOCKED, "retry-after")
    assert "site.example/page" not in site.hits
    clock.now += 121
    assert (await f.fetch("http://site.example/page")).outcome == Outcome.OK


async def test_retry_after_as_an_http_date_is_honoured(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, _ = world
    clock = Clock()
    f = polite(session, clock)
    await f.fetch("http://site.example/busy?after=Wed, 21 Oct 2099 07:28:00 GMT")
    held = await f.fetch("http://site.example/page")
    assert held.reason == "retry-after"
    clock.now += MAX_RETRY_AFTER_S + 1  # a far-future date is capped at three days
    assert (await f.fetch("http://site.example/page")).outcome == Outcome.OK


async def test_a_503_with_retry_after_holds_the_host_and_other_hosts_are_unaffected(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, _ = world
    f = polite(session)
    await f.fetch("http://site.example/down")
    assert (await f.fetch("http://site.example/page")).reason == "retry-after"
    assert (await f.fetch("http://h0.example/page")).outcome == Outcome.OK


async def test_a_nonsense_retry_after_is_ignored(world: tuple[aiohttp.ClientSession, Site]) -> None:
    session, _ = world
    f = polite(session)
    await f.fetch("http://site.example/busy?after=soon")
    assert (await f.fetch("http://site.example/page")).outcome == Outcome.OK


# --- attempts and concurrency ---


async def test_a_url_gets_at_most_three_attempts(world: tuple[aiohttp.ClientSession, Site]) -> None:
    session, site = world
    f = polite(session)
    for _ in range(3):
        assert (await f.fetch("http://site.example/broken")).outcome == Outcome.BLOCKED
    fourth = await f.fetch("http://site.example/broken")
    assert (fourth.outcome, fourth.reason) == (Outcome.BLOCKED, "attempt limit")
    assert site.hits.count("site.example/broken") == 3


async def test_one_request_per_host_and_four_in_total_at_a_time(
    world: tuple[aiohttp.ClientSession, Site],
) -> None:
    session, site = world
    f = polite(session)
    same_host = [f"http://site.example/slow?n={i}" for i in range(3)]
    spread = [f"http://h{i}.example/slow" for i in range(6)]
    await asyncio.gather(*(f.fetch(u) for u in same_host + spread))
    assert site.peak["site.example"] == 1
    assert site.total_peak <= 4
