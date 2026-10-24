"""Polite fetching: robots.txt, Retry-After per host, and a cap on attempts per URL.

- robots.txt is fetched through the same guarded session and loop as pages, so the
  same address, port, redirect, deadline and size rules apply (a robots.txt that
  redirects to an internal address is refused like any page). It is cached per host
  for a day. A 4xx means "no rules, allow all"; a 5xx, timeout or refusal means
  "disallow all" for the day, as major crawlers treat it.
- A 429 or 503 with Retry-After (seconds or an HTTP date, capped at three days) holds
  that host: nothing is sent to it before then.
- Each URL gets at most MAX_ATTEMPTS fetches in this process; the pipeline keeps the
  same count across nights.
- Concurrency (four at a time, one per host) is enforced by the connector.
"""

import time
import urllib.robotparser
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import aiohttp

from learning_tracker.fetcher.fetch import (
    FETCH_DEADLINE_S,
    BodyRule,
    FetchResult,
    Outcome,
    fetch_page,
)
from learning_tracker.fetcher.urls import UnsafeURL, validate_url

ROBOTS_AGENT_NAME = "learning-tracker"  # the name robots.txt groups address us by
ROBOTS_TTL_S = 24 * 3600
ROBOTS = BodyRule(("text/",), 512 * 1024)
MAX_RETRY_AFTER_S = 3 * 24 * 3600
MAX_ATTEMPTS = 3

Clock = Callable[[], float]


class PoliteFetcher:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        clock: Clock = time.monotonic,
        deadline: float = FETCH_DEADLINE_S,
    ) -> None:
        self._session = session
        self._clock = clock
        self._deadline = deadline
        self._robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self._held_until: dict[str, float] = {}
        self._attempts: dict[str, int] = {}

    async def fetch(self, url: str) -> FetchResult:
        try:
            target = validate_url(url)
        except UnsafeURL as exc:
            return FetchResult(url, Outcome.UNSAFE, str(exc))
        host, now = target.host, self._clock()
        if self._held_until.get(host, 0.0) > now:
            return FetchResult(url, Outcome.BLOCKED, "retry-after")
        if self._attempts.get(url, 0) >= MAX_ATTEMPTS:
            return FetchResult(url, Outcome.BLOCKED, "attempt limit")
        if not await self._robots_allow(target.scheme, host, str(target.url)):
            return FetchResult(url, Outcome.BLOCKED, "robots.txt")
        self._attempts[url] = self._attempts.get(url, 0) + 1
        result = await fetch_page(self._session, url, deadline=self._deadline)
        if result.status in (429, 503):
            wait = retry_after_seconds(result.retry_after)
            if wait:
                self._held_until[host] = self._clock() + wait
        return result

    async def _robots_allow(self, scheme: str, host: str, url: str) -> bool:
        cached = self._robots.get(host)
        if cached is None or cached[0] <= self._clock():
            rules = await self._load_robots(scheme, host)
            self._robots[host] = (self._clock() + ROBOTS_TTL_S, rules)
            cached = self._robots[host]
        rules = cached[1]
        return rules is not None and rules.can_fetch(ROBOTS_AGENT_NAME, url)

    async def _load_robots(
        self, scheme: str, host: str
    ) -> urllib.robotparser.RobotFileParser | None:
        """The host's rules, an allow-all parser for a 4xx, or None for "disallow all"."""
        robots_url = f"{scheme}://{host}/robots.txt"
        result = await fetch_page(self._session, robots_url, deadline=self._deadline, body=ROBOTS)
        parser = urllib.robotparser.RobotFileParser()
        if result.outcome == Outcome.OK and result.body is not None:
            parser.parse(result.body.decode("utf-8", "replace").splitlines())
            return parser
        if result.status is not None and 400 <= result.status < 500 and result.status != 429:
            parser.parse([])  # no file: no rules
            return parser
        return None  # 5xx, 429, timeout, refused or unsafe: disallow the host for a day


def retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float:
    """Seconds to wait from a Retry-After header, capped; 0 when absent or unreadable."""
    if not value:
        return 0.0
    value = value.strip()
    if value.isdigit():
        seconds = float(value)
    else:
        try:
            when = parsedate_to_datetime(value)
        except TypeError, ValueError:
            return 0.0
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    return max(0.0, min(seconds, MAX_RETRY_AFTER_S))
