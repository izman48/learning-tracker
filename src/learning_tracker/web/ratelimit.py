"""Fixed-window rate limits kept in Postgres.

A bucket key is `<name>:<HMAC(ip key, subject)>`, so the table never holds a raw IP
or email. Each hit is one atomic upsert in its own short transaction: it counts even
when the request then fails (a wrong password), and two web processes share counts.
"""

import hashlib
import hmac
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import IPv6Address, ip_address, ip_network

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

TOO_MANY = "Too many tries. Wait a few minutes, then try again."  # spec AUTH-5/9

# Windows are aligned and timed on the database's clock only, so web processes and
# hosts with different clocks agree on them.
_HIT = text(
    """
    INSERT INTO rate_limit_buckets (key, window_start, count)
    VALUES (:key, to_timestamp(floor(extract(epoch FROM now()) / :seconds) * :seconds), 1)
    ON CONFLICT (key) DO UPDATE SET
      count = CASE WHEN rate_limit_buckets.window_start = EXCLUDED.window_start
                   THEN rate_limit_buckets.count + 1 ELSE 1 END,
      window_start = EXCLUDED.window_start
    RETURNING count,
      ceil(extract(epoch FROM window_start + make_interval(secs => :seconds) - now()))::int
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
# Sign-up and invite-code guessing: per IP, and a ceiling for the whole instance.
SIGNUP_PER_IP = Limit("signup-ip", 10, timedelta(hours=1))
SIGNUP_PER_INSTANCE = Limit("signup-instance", 100, timedelta(hours=1))
# Reset tokens are 256 bits, so this only slows noise and protects the hashing slots.
RESET_PER_IP = Limit("reset-ip", 10, timedelta(hours=1))


def ip_subject(ip: str) -> str:
    """IPv4 per address; IPv6 per /64, since one host can hold a whole /64."""
    try:
        address = ip_address(ip)
    except ValueError:
        return ip
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ip_network(f"{address}/64", strict=False))
    return str(address)


def account_subject(user_id: uuid.UUID | None, typed_email: str) -> str:
    """A found account is keyed by its id; an unknown email by a normalised form, so
    case and Unicode variants of one address share a bucket either way."""
    if user_id is not None:
        return f"user:{user_id}"
    return "email:" + unicodedata.normalize("NFKC", typed_email).strip().casefold()


class RateLimiter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], key: bytes) -> None:
        self._sessions = session_factory
        self._key = key

    def bucket_key(self, limit: Limit, subject: str) -> str:
        digest = hmac.new(self._key, subject.encode(), hashlib.sha256).hexdigest()
        return f"{limit.name}:{digest[:40]}"

    async def hit(self, limit: Limit, subject: str) -> int | None:
        """Count one attempt. None if allowed, else the seconds until the window ends."""
        params = {"key": self.bucket_key(limit, subject), "seconds": limit.window.total_seconds()}
        async with self._sessions() as session:
            count, remaining = (await session.execute(_HIT, params)).one()
            await session.commit()
        if count <= limit.max_hits:
            return None
        return max(1, int(remaining))
