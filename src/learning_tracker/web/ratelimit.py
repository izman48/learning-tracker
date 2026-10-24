"""Fixed-window rate limits kept in Postgres.

A bucket key is `<name>:<HMAC(ip key, subject)>`, so the table never holds a raw IP
or email. Each hit is one atomic upsert in its own short transaction: it counts even
when the request then fails (a wrong password), and two web processes share counts.
"""

import hashlib
import hmac
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

TOO_MANY = "Too many tries. Wait a few minutes, then try again."  # spec AUTH-5/9

_HIT = text(
    """
    INSERT INTO rate_limit_buckets (key, window_start, count)
    VALUES (:key, :window_start, 1)
    ON CONFLICT (key) DO UPDATE SET
      count = CASE WHEN rate_limit_buckets.window_start = EXCLUDED.window_start
                   THEN rate_limit_buckets.count + 1 ELSE 1 END,
      window_start = EXCLUDED.window_start
    RETURNING count
    """
)


@dataclass(frozen=True)
class Limit:
    name: str
    max_hits: int
    window: timedelta


# Sign-in: per IP slows spraying; per account slows guessing one password from many IPs.
LOGIN_PER_IP = Limit("login-ip", 5, timedelta(minutes=1))
# Every password re-entry (sign-in, change password, later delete account) shares this.
PASSWORD_PER_ACCOUNT = Limit("password-account", 10, timedelta(minutes=15))


class RateLimiter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], key: bytes) -> None:
        self._sessions = session_factory
        self._key = key

    def bucket_key(self, limit: Limit, subject: str) -> str:
        digest = hmac.new(self._key, subject.encode(), hashlib.sha256).hexdigest()
        return f"{limit.name}:{digest[:40]}"

    async def hit(self, limit: Limit, subject: str) -> int | None:
        """Count one attempt. None if allowed, else the seconds until the window ends."""
        now = datetime.now(UTC)
        seconds = limit.window.total_seconds()
        start = datetime.fromtimestamp(math.floor(now.timestamp() / seconds) * seconds, UTC)
        async with self._sessions() as session:
            count = (
                await session.execute(
                    _HIT, {"key": self.bucket_key(limit, subject), "window_start": start}
                )
            ).scalar_one()
            await session.commit()
        if count <= limit.max_hits:
            return None
        return max(1, math.ceil((start + limit.window - now).total_seconds()))
