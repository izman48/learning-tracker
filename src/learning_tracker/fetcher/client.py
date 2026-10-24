"""The fetcher's HTTP session, guarded at two independent layers.

1. This session runs `validate_url` on the first request and on every redirect hop's
   Location, before aiohttp follows it. It refuses early, with a clear reason.
2. The connector (`safe_connector`) refuses any non-public address at DNS and again at
   the socket, which is the real guarantee: it also covers IP literals, which aiohttp
   never sends through the resolver.

Each layer is tested on its own, so neither silently depends on the other.
"""

from types import SimpleNamespace
from typing import Any, Protocol

import aiohttp

from learning_tracker.fetcher.urls import TargetURL, validate_url


class UrlCheck(Protocol):
    def __call__(self, raw: str, *, base: str | None = None) -> TargetURL: ...


def url_guard(check_url: UrlCheck = validate_url) -> aiohttp.TraceConfig:
    async def on_start(
        _session: aiohttp.ClientSession,
        _ctx: SimpleNamespace,
        params: aiohttp.TraceRequestStartParams,
    ) -> None:
        check_url(str(params.url))

    async def on_redirect(
        _session: aiohttp.ClientSession,
        _ctx: SimpleNamespace,
        params: aiohttp.TraceRequestRedirectParams,
    ) -> None:
        # on_request_start fires only for the first request; each later hop is seen here.
        location = params.response.headers.get("Location", "")
        check_url(location, base=str(params.url))

    trace = aiohttp.TraceConfig()
    trace.on_request_start.append(on_start)
    trace.on_request_redirect.append(on_redirect)
    return trace


def guarded_session(
    *, connector: aiohttp.BaseConnector, check_url: UrlCheck = validate_url, **kwargs: Any
) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=connector, trace_configs=[url_guard(check_url)], **kwargs
    )
