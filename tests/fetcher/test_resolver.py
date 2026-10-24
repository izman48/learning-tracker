"""The DNS step: a host is refused if any answer is not public, and the connection
then uses only the vetted answer. Every refusal has a positive control through the
same harness."""

import socket
from collections.abc import AsyncIterator, Callable

import aiohttp
import pytest
from aiohttp import web
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.test_utils import TestServer

from learning_tracker.fetcher.addresses import is_public_address
from learning_tracker.fetcher.resolver import SafeResolver, UnsafeAddress, safe_connector

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"  # aiohttp runs on asyncio only


class FakeDNS(AbstractResolver):
    """Answers from a table, and records each lookup."""

    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = table
        self.lookups: list[str] = []

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC
    ) -> list[ResolveResult]:
        self.lookups.append(host)
        if host not in self.table:
            raise OSError(f"no such host {host}")
        return [
            ResolveResult(
                hostname=host,
                host=ip,
                port=port,
                family=socket.AF_INET6 if ":" in ip else socket.AF_INET,
                proto=0,
                flags=0,
            )
            for ip in self.table[host]
        ]

    async def close(self) -> None:
        pass


DNS = {
    "public.example": ["93.184.215.14"],
    "public6.example": ["2606:4700:4700::1111"],
    "meta.example": ["169.254.169.254"],
    "loop.example": ["127.0.0.1"],
    "loop6.example": ["::1"],
    "private.example": ["10.0.0.1"],
    "nat64.example": ["64:ff9b::a9fe:a9fe"],
    "sixtofour.example": ["2002:a9fe:a9fe::"],
    "teredo.example": ["2001:0:4136:e378:8000:63bf:3fff:fdd2"],
    "sitelocal.example": ["fec0::1"],
    "mapped.example": ["::ffff:127.0.0.1"],
    "mixed.example": ["93.184.215.14", "10.0.0.1"],
    "mixed6.example": ["2606:4700:4700::1111", "fe80::1"],
    "empty.example": [],
}


async def test_public_answers_pass_through_unchanged() -> None:
    resolver = SafeResolver(FakeDNS(DNS))
    assert [a["host"] for a in await resolver.resolve("public.example", 443)] == ["93.184.215.14"]
    assert [a["host"] for a in await resolver.resolve("public6.example", 443)] == [
        "2606:4700:4700::1111"
    ]


@pytest.mark.parametrize(
    "host",
    [
        "meta.example",
        "loop.example",
        "loop6.example",
        "private.example",
        "nat64.example",
        "sixtofour.example",
        "teredo.example",
        "sitelocal.example",
        "mapped.example",
        "mixed.example",
        "mixed6.example",
        "empty.example",
    ],
)
async def test_refuses_a_host_if_any_answer_is_not_public(host: str) -> None:
    resolver = SafeResolver(FakeDNS(DNS))
    assert await resolver.resolve("public.example", 443)  # control through the same resolver
    with pytest.raises(UnsafeAddress):
        await resolver.resolve(host, 443)


async def test_a_dns_failure_is_not_reported_as_unsafe() -> None:
    with pytest.raises(OSError) as info:
        await SafeResolver(FakeDNS(DNS)).resolve("nxdomain.example", 443)
    assert not isinstance(info.value, UnsafeAddress)


# --- through a real aiohttp session and a local server ---


@pytest.fixture
async def local_server() -> AsyncIterator[TestServer]:
    async def ok(_request: web.Request) -> web.Response:
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", ok)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    yield server
    await server.close()


def only_the_harness_is_public(harness_ip: str) -> Callable[[str], bool]:
    """Test policy: the local server stands in for a public host; nothing else changes."""
    return lambda ip: ip == harness_ip or is_public_address(ip)


async def test_session_refuses_metadata_host_and_still_reaches_the_control(
    local_server: TestServer,
) -> None:
    port = local_server.port
    dns = FakeDNS({"control.example": ["127.0.0.1"], "meta.example": ["169.254.169.254"]})
    connector = safe_connector(dns, is_allowed=only_the_harness_is_public("127.0.0.1"))
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(f"http://control.example:{port}/") as resp:
            assert resp.status == 200 and await resp.text() == "hello"
        with pytest.raises(aiohttp.ClientConnectorError) as info:
            await session.get(f"http://meta.example:{port}/")
    assert isinstance(info.value.os_error, UnsafeAddress)
    assert dns.lookups == ["control.example", "meta.example"]


async def test_the_connector_vets_every_request_afresh(local_server: TestServer) -> None:
    # No DNS cache: a host that later resolves somewhere private cannot reuse an old answer.
    dns = FakeDNS({"control.example": ["127.0.0.1"]})
    connector = safe_connector(dns, is_allowed=only_the_harness_is_public("127.0.0.1"))
    url = f"http://control.example:{local_server.port}/"
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(url, headers={"Connection": "close"}) as resp:
            await resp.read()
        dns.table["control.example"] = ["169.254.169.254"]  # the "rebind"
        with pytest.raises(aiohttp.ClientConnectorError) as info:
            await session.get(url)
    assert isinstance(info.value.os_error, UnsafeAddress)
    assert dns.lookups == ["control.example", "control.example"]


# --- IP-literal hosts skip the resolver, so the connector also vets every socket ---


def caused_by_unsafe_address(exc: BaseException) -> bool:
    """True if UnsafeAddress is anywhere in the exception's cause chain or group."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, UnsafeAddress):
            return True
        stack += [x for x in (e.__cause__, e.__context__, getattr(e, "os_error", None)) if x]
        stack += list(getattr(e, "exceptions", ()))
    return False


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:{port}/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://[::1]:{port}/",
        "http://[::ffff:127.0.0.1]:{port}/",
    ],
)
async def test_ip_literal_hosts_are_refused_at_connect_time(
    local_server: TestServer, url: str
) -> None:
    timeout = aiohttp.ClientTimeout(total=3)  # an unvetted connect must not hang the suite
    async with aiohttp.ClientSession(connector=safe_connector(), timeout=timeout) as session:
        with pytest.raises(aiohttp.ClientConnectorError) as info:
            await session.get(url.format(port=local_server.port))
    assert caused_by_unsafe_address(info.value)


async def test_literal_control_reaches_the_harness_when_policy_allows_it(
    local_server: TestServer,
) -> None:
    connector = safe_connector(is_allowed=only_the_harness_is_public("127.0.0.1"))
    url = f"http://127.0.0.1:{local_server.port}/"
    async with aiohttp.ClientSession(connector=connector) as session, session.get(url) as resp:
        assert resp.status == 200


async def test_a_redirect_to_an_ip_literal_is_refused_at_the_second_hop() -> None:
    hits: list[str] = []

    async def jump(request: web.Request) -> web.Response:
        hits.append(request.path)
        # 127.0.0.2 is loopback too; the policy allows only the harness's 127.0.0.1.
        raise web.HTTPFound(f"http://127.0.0.2:{request.url.port}/inside")

    app = web.Application()
    app.router.add_get("/", jump)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        dns = FakeDNS({"public.example": ["127.0.0.1"]})
        connector = safe_connector(dns, is_allowed=only_the_harness_is_public("127.0.0.1"))
        async with aiohttp.ClientSession(connector=connector) as session:
            with pytest.raises(aiohttp.ClientConnectorError) as info:
                await session.get(f"http://public.example:{server.port}/", allow_redirects=True)
    finally:
        await server.close()
    assert hits == ["/"]  # the first hop was served (control), the second never connected
    assert caused_by_unsafe_address(info.value)
