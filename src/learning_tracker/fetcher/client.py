"""The fetcher's HTTP session, guarded at two independent layers.

1. This session runs `validate_url` on the first request and on every redirect hop's
   Location, before aiohttp follows it. It refuses early, with a clear reason.
2. The connector (`safe_connector`) refuses any non-public address at DNS and again at
   the socket, which is the real guarantee: it also covers IP literals, which aiohttp
   never sends through the resolver.

Each layer is tested on its own, so neither silently depends on the other.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Protocol

import aiohttp
from aiohttp.abc import AbstractResolver

from learning_tracker.fetcher.addresses import is_public_address
from learning_tracker.fetcher.resolver import safe_connector
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


# Per-step limits; the whole fetch, across redirects and the body, has its own deadline.
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 10


def fetcher_session(
    contact_url: str,
    *,
    resolver: AbstractResolver | None = None,
    is_allowed: Callable[[str], bool] = is_public_address,
) -> aiohttp.ClientSession:
    """The one session the fetcher uses: both guards, no cookies, no proxy settings
    from the environment, no credentials, and an honest User-Agent."""
    return guarded_session(
        connector=safe_connector(resolver, is_allowed=is_allowed),
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
        timeout=aiohttp.ClientTimeout(
            total=None, sock_connect=CONNECT_TIMEOUT_S, sock_read=READ_TIMEOUT_S
        ),
        headers={
            "User-Agent": f"Mozilla/5.0 (compatible; learning-tracker; +{contact_url})",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            "Accept-Language": "en",
            "Accept-Encoding": "gzip, deflate",
        },
    )
