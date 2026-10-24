"""The fetcher's DNS step and connector.

`SafeResolver` wraps a real resolver and refuses the whole host if any A/AAAA answer
is not public. aiohttp then connects to the addresses this resolver returned, so
there is no second lookup and no DNS-rebinding window. The connector disables
aiohttp's DNS cache so every request is vetted afresh.
"""

import socket
from collections.abc import Callable

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from learning_tracker.fetcher.addresses import is_public_address


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


def safe_connector(
    inner: AbstractResolver | None = None, *, is_allowed: Callable[[str], bool] = is_public_address
) -> aiohttp.TCPConnector:
    resolver = SafeResolver(inner or aiohttp.ThreadedResolver(), is_allowed=is_allowed)
    return aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False, limit=4, limit_per_host=1)
