"""Single-use password reset tokens, shared by admin links and (later) email.

The token is 32 random bytes, sent only in a URL fragment (`/reset#token=…`), so it
never appears in a request line or a log. The database keeps HMAC(reset subkey,
token) in password_resets, one row per user: a new token replaces the old one,
which revokes it. Consuming is one DELETE ... RETURNING, so a token works once
even under a race. Expiry is set and checked on the database's clock.
"""

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from learning_tracker.db.models import PasswordChangeSource, PasswordReset

_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{43}")


def _hash(key: bytes, token: str) -> bytes:
    return hmac.new(key, token.encode(), hashlib.sha256).digest()


async def issue_reset(
    db: AsyncSession,
    key: bytes,
    user_id: uuid.UUID,
    *,
    minutes: int,
    created_by: uuid.UUID | None,
) -> str:
    """Make a token for this user, replacing (revoking) any earlier one."""
    token = secrets.token_urlsafe(32)
    values = {
        "token_hash": _hash(key, token),
        "expires_at": func.now() + timedelta(minutes=minutes),
        "created_by": created_by,
    }
    await db.execute(
        insert(PasswordReset)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(index_elements=[PasswordReset.user_id], set_=values)
    )
    return token


async def consume_reset(
    db: AsyncSession, key: bytes, token: str
) -> tuple[uuid.UUID, PasswordChangeSource] | None:
    """Spend a live token: the user it was for and how it was issued, or None."""
    if not _TOKEN_SHAPE.fullmatch(token):
        return None
    row = (
        await db.execute(
            delete(PasswordReset)
            .where(
                PasswordReset.token_hash == _hash(key, token), PasswordReset.expires_at > func.now()
            )
            .returning(PasswordReset.user_id, PasswordReset.created_by)
        )
    ).one_or_none()
    if row is None:
        return None
    user_id, created_by = row
    via = PasswordChangeSource.ADMIN_RESET if created_by else PasswordChangeSource.EMAIL_RESET
    return user_id, via
