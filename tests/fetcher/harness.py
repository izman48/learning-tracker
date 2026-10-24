"""Shared test doubles for the fetcher: a table-driven DNS and the harness policy.

`FakeDNS(port=...)` can point every answer at the local test server's real port, so
test URLs keep their normal port (80) and pass `validate_url` unchanged.
"""

import socket
from collections.abc import Callable

from aiohttp.abc import AbstractResolver, ResolveResult

from learning_tracker.fetcher.addresses import is_public_address


class FakeDNS(AbstractResolver):
    """Answers from a table, and records each lookup."""

    def __init__(self, table: dict[str, list[str]], *, port: int | None = None) -> None:
        self.table = table
        self.port = port
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
                port=self.port if self.port is not None else port,
                family=socket.AF_INET6 if ":" in ip else socket.AF_INET,
                proto=0,
                flags=0,
            )
            for ip in self.table[host]
        ]

    async def close(self) -> None:
        pass


def only_the_harness_is_public(harness_ip: str) -> Callable[[str], bool]:
    """Test policy: the local server stands in for a public host; nothing else changes."""
    return lambda ip: ip == harness_ip or is_public_address(ip)
