"""Server-side, revocable sessions behind a host-only cookie.

The cookie carries 32 random bytes (base64url). The database stores only
HMAC-SHA256(session subkey, token), so reading the sessions table yields nothing a
browser can present, and a token is useless under a different LT_SECRET_KEY.
fastapi-users' own DatabaseStrategy stores the raw token, which is why this exists.
"""

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi_users.authentication import CookieTransport
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from learning_tracker.db.models import Session, User

# __Host- makes the browser refuse the cookie unless it is Secure, Path=/ and has no
# Domain, so an app on a sibling subdomain can neither read nor plant it.
COOKIE_NAME = "__Host-lt_session"
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{43}")


def cookie_transport(lifetime: timedelta) -> CookieTransport:
    return CookieTransport(
        cookie_name=COOKIE_NAME,
        cookie_max_age=int(lifetime.total_seconds()),
        cookie_path="/",
        cookie_domain=None,
        cookie_secure=True,
        cookie_httponly=True,
        cookie_samesite="lax",
    )


class SessionStore:
    def __init__(self, db: AsyncSession, *, session_key: bytes, lifetime: timedelta) -> None:
        self.db = db
        self._key = session_key
        self.lifetime = lifetime

    def hash(self, token: str) -> bytes:
        return hmac.new(self._key, token.encode(), hashlib.sha256).digest()

    async def issue(self, user_id: uuid.UUID, *, replacing: str | None = None) -> str:
        """Start a session. `replacing` is the cookie the browser presented, if any:
        it is ended first, so signing in always rotates the session id."""
        if replacing is not None and _TOKEN_SHAPE.fullmatch(replacing):
            await self.db.execute(
                delete(Session).where(
                    Session.token_hash == self.hash(replacing), Session.user_id == user_id
                )
            )
        token = secrets.token_urlsafe(32)
        self.db.add(
            Session(
                token_hash=self.hash(token),
                user_id=user_id,
                expires_at=datetime.now(UTC) + self.lifetime,
            )
        )
        await self.db.commit()
        return token

    async def user_id_for(self, token: str | None) -> uuid.UUID | None:
        """The active user a live session belongs to, or None."""
        if token is None or not _TOKEN_SHAPE.fullmatch(token):
            return None
        query = (
            select(Session.user_id)
            .join(User, User.id == Session.user_id)
            .where(
                Session.token_hash == self.hash(token),
                Session.expires_at > datetime.now(UTC),
                User.is_active.is_(True),
            )
        )
        return (await self.db.execute(query)).scalar_one_or_none()

    async def revoke(self, token: str) -> None:
        if _TOKEN_SHAPE.fullmatch(token):
            await self.db.execute(delete(Session).where(Session.token_hash == self.hash(token)))
            await self.db.commit()

    async def revoke_others(self, user_id: uuid.UUID, *, keep: str) -> None:
        """After a password change: end every session of the user except this one."""
        await self.db.execute(
            delete(Session).where(Session.user_id == user_id, Session.token_hash != self.hash(keep))
        )
        await self.db.commit()

    async def revoke_all(self, user_id: uuid.UUID) -> None:
        await self.db.execute(delete(Session).where(Session.user_id == user_id))
        await self.db.commit()

    async def purge_expired(self) -> int:
        result = cast(
            CursorResult[Any],
            await self.db.execute(delete(Session).where(Session.expires_at <= datetime.now(UTC))),
        )
        await self.db.commit()
        return result.rowcount
