"""Fetch one page: follow redirects by hand, re-check every hop, classify the result.

Outcome classes (what the pipeline does with each is decided elsewhere):
- ok: 2xx HTML or XHTML, body read (decompressed, capped).
- dead: 404/410 and other 4xx, DNS failure, refused connection, broken response,
  a redirect with no Location, or more than MAX_REDIRECTS redirects.
- walled: 401 (login wall) or 451 (legal block). Final; not retried.
- unsafe: a URL or address the fetcher must not reach, a redirect to another site,
  or invalid TLS. Never retried.
- not_article: not HTML (a PDF is reported as "pdf"), or a body over the size cap.
- blocked: 403, 429, 5xx, or a bot-challenge page. Unverified, so retried later.
- timeout: the whole fetch passed its deadline. Unverified, so retried later.
"""

import asyncio
import ssl
from dataclasses import dataclass
from enum import StrEnum

import aiohttp

from learning_tracker.fetcher.domains import same_site
from learning_tracker.fetcher.resolver import UnsafeAddress
from learning_tracker.fetcher.urls import TargetURL, UnsafeURL, validate_url

MAX_REDIRECTS = 5
FETCH_DEADLINE_S = 15  # covers every redirect hop and the body read
MAX_BODY_BYTES = 5 * 1024 * 1024  # decompressed, so a compression bomb stops here too
ARTICLE_TYPES = ("text/html", "application/xhtml+xml")
_REDIRECTS = (301, 302, 303, 307, 308)


class Outcome(StrEnum):
    OK = "ok"
    DEAD = "dead"
    WALLED = "walled"
    UNSAFE = "unsafe"
    NOT_ARTICLE = "not_article"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class FetchResult:
    url: str
    outcome: Outcome
    reason: str = ""
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    body: bytes | None = None
    retry_after: str | None = None


async def fetch_page(
    session: aiohttp.ClientSession, url: str, *, deadline: float = FETCH_DEADLINE_S
) -> FetchResult:
    try:
        async with asyncio.timeout(deadline):
            return await _follow(session, url)
    except TimeoutError:
        return FetchResult(url, Outcome.TIMEOUT, "deadline")
    except UnsafeURL as exc:
        return FetchResult(url, Outcome.UNSAFE, str(exc))
    except aiohttp.ClientError as exc:
        outcome, reason = classify_client_error(exc)
        return FetchResult(url, outcome, reason)


async def _follow(session: aiohttp.ClientSession, url: str) -> FetchResult:
    target = validate_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        async with session.get(target.url, allow_redirects=False) as resp:
            if resp.status not in _REDIRECTS:
                return await _final(url, target, resp)
            location = resp.headers.get("Location")
        if not location:
            return FetchResult(url, Outcome.DEAD, "redirect without location", resp.status)
        target = _next_hop(target, location)
    return FetchResult(url, Outcome.DEAD, "too many redirects")


def _next_hop(current: TargetURL, location: str) -> TargetURL:
    hop = validate_url(location, base=str(current.url))
    if not same_site(current.host, hop.host):
        raise UnsafeURL("redirect to another site")
    return hop


async def _final(url: str, target: TargetURL, resp: aiohttp.ClientResponse) -> FetchResult:
    content_type = resp.content_type.lower()
    seen = FetchResult(
        url,
        Outcome.OK,
        status=resp.status,
        final_url=str(target.url),
        content_type=content_type,
        retry_after=resp.headers.get("Retry-After"),
    )
    blocked = _blocked_reason(resp)
    if blocked:
        return _with(seen, Outcome.BLOCKED, blocked)
    if resp.status in (401, 451):
        return _with(seen, Outcome.WALLED, f"http {resp.status}")
    if resp.status >= 400 or resp.status < 200:
        return _with(seen, Outcome.DEAD, f"http {resp.status}")
    if content_type == "application/pdf":
        return _with(seen, Outcome.NOT_ARTICLE, "pdf")
    if content_type not in ARTICLE_TYPES:
        return _with(seen, Outcome.NOT_ARTICLE, f"not html ({content_type or 'none'})")
    body = await _read_capped(resp)
    if body is None:
        return _with(seen, Outcome.NOT_ARTICLE, "too large")
    return FetchResult(**{**seen.__dict__, "body": body})


def _blocked_reason(resp: aiohttp.ClientResponse) -> str:
    if resp.headers.get("cf-mitigated", "").lower() == "challenge":
        return "bot challenge"
    if resp.status in (403, 429) or resp.status >= 500:
        return f"http {resp.status}"
    return ""


async def _read_capped(resp: aiohttp.ClientResponse) -> bytes | None:
    chunks, size = [], 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _with(result: FetchResult, outcome: Outcome, reason: str) -> FetchResult:
    return FetchResult(**{**result.__dict__, "outcome": outcome, "reason": reason, "body": None})


def classify_client_error(exc: aiohttp.ClientError) -> tuple[Outcome, str]:
    if _caused_by(exc, UnsafeAddress):
        return Outcome.UNSAFE, "non-public address"
    if isinstance(exc, aiohttp.ClientSSLError) or _caused_by(exc, ssl.SSLError):
        return Outcome.UNSAFE, "invalid tls"
    if isinstance(exc, aiohttp.ClientConnectorDNSError):
        return Outcome.DEAD, "dns"
    if isinstance(exc, aiohttp.ClientConnectorError):
        return Outcome.DEAD, "connection failed"
    if isinstance(exc, aiohttp.ServerTimeoutError):
        return Outcome.TIMEOUT, "read timeout"
    return Outcome.DEAD, "broken response"


def _caused_by(exc: BaseException, kind: type[BaseException]) -> bool:
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, kind):
            return True
        stack += [x for x in (e.__cause__, e.__context__, getattr(e, "os_error", None)) if x]
        stack += list(getattr(e, "exceptions", ()))
    return False
