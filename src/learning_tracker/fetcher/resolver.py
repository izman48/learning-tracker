"""The fetcher's DNS step and connector.

`SafeResolver` wraps a real resolver and refuses the whole host if any A/AAAA answer
is not public; aiohttp then connects to exactly those answers, so there is no second
lookup and no DNS-rebinding window, and the DNS cache is off so every request is
vetted afresh. aiohttp never sends IP-literal hosts through a resolver, so the
connector also checks the exact address of every socket it opens.
"""

import socket
from collections.abc import Callable
from typing import Any

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from learning_tracker.fetcher.addresses import is_public_address

# Politeness: at most 4 fetches in flight, and one at a time per host.
MAX_CONNECTIONS = 4
MAX_CONNECTIONS_PER_HOST = 1


class UnsafeAddress(OSError):
    """The host resolves to an address the fetcher must not reach.

    Subclasses OSError because aiohttp only wraps OSError from a resolver into
    ClientConnectorError; anything else would escape as an unexpected crash.
    """


class SafeResolver(AbstractResolver):
    def __init__(
        self, inner: AbstractResolver, *, is_allowed: Callable[[str], bool] = is_public_address
    ):
        self._inner = inner
        self._is_allowed = is_allowed

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC
    ) -> list[ResolveResult]:
        answers = await self._inner.resolve(host, port, family)
        if not answers:
            raise UnsafeAddress("unsafe: no address for host")
        if not all(self._is_allowed(a["host"]) for a in answers):
            raise UnsafeAddress("unsafe: host resolves to a non-public address")
        return answers

    async def close(self) -> None:
        await self._inner.close()


AddrInfo = tuple[int | socket.AddressFamily, int | socket.SocketKind, int, str, tuple[Any, ...]]


def vetted_socket_factory(is_allowed: Callable[[str], bool]) -> Callable[[AddrInfo], socket.socket]:
    """Check the exact address of every connection attempt before a socket exists.

    aiohttp skips the resolver for IP-literal hosts, so this is the layer that holds
    for literals, redirect hops and Happy Eyeballs attempts alike.
    """

    def make_socket(addr_info: AddrInfo) -> socket.socket:
        family, type_, proto, _, sockaddr = addr_info
        if not is_allowed(str(sockaddr[0])):
            raise UnsafeAddress("unsafe: connect to a non-public address")
        return socket.socket(family, type_, proto)

    return make_socket


def safe_connector(
    inner: AbstractResolver | None = None, *, is_allowed: Callable[[str], bool] = is_public_address
) -> aiohttp.TCPConnector:
    """A connector that refuses non-public addresses twice: at DNS (any bad answer
    refuses the host) and at connect (the exact address of each socket)."""
    resolver = SafeResolver(inner or aiohttp.ThreadedResolver(), is_allowed=is_allowed)
    return aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=False,
        limit=MAX_CONNECTIONS,
        limit_per_host=MAX_CONNECTIONS_PER_HOST,
        socket_factory=vetted_socket_factory(is_allowed),
    )
