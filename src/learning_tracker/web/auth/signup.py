"""Create an account, redeeming an invite code in the same transaction.

Sign-ups take a transaction-scoped advisory lock, so the user cap is exact and two
sign-ups cannot both pass it. The code is redeemed with one conditional UPDATE, so
`uses` can never pass `max_uses` (the table's CHECK is the backstop). If the user
insert then fails, for example because the email exists, the whole transaction
rolls back and the code is not consumed.
"""

import uuid
from typing import Literal

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from learning_tracker.db.models import InviteCode, InviteRedemption, User
from learning_tracker.web.auth.invites import InvalidCode, code_hash
from learning_tracker.web.config import WebConfig

_SIGNUP_LOCK = 0x6C74_5349_474E  # arbitrary, fixed: "ltSIGN"

Reason = Literal["code", "exists", "closed"]


class SignupRefused(Exception):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


async def create_account(
    db: AsyncSession,
    config: WebConfig,
    *,
    email: str,
    hashed_password: str,
    timezone: str,
    code: str | None,
) -> uuid.UUID:
    """Insert the user (and redemption) and flush; the caller commits."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SIGNUP_LOCK})
    users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    if users >= config.max_users:
        raise SignupRefused("closed")

    code_id = await _redeem(db, config, code) if config.invite_required else None
    try:
        async with db.begin_nested():
            user_id = (
                await db.execute(
                    insert(User)
                    .values(email=email, hashed_password=hashed_password, timezone=timezone)
                    .returning(User.id)
                )
            ).scalar_one()
    except IntegrityError:
        await db.rollback()  # also undoes the redemption
        raise SignupRefused("exists") from None
    if code_id is not None:
        await db.execute(insert(InviteRedemption).values(code_id=code_id, user_id=user_id))
    await db.flush()
    return user_id


async def _redeem(db: AsyncSession, config: WebConfig, code: str | None) -> uuid.UUID:
    if code is None:
        raise SignupRefused("code")
    try:
        hashed = code_hash(config.invite_key, code)
    except InvalidCode:
        raise SignupRefused("code") from None
    redeemed = await db.execute(
        update(InviteCode)
        .where(
            InviteCode.code_hash == hashed,
            InviteCode.disabled_at.is_(None),
            (InviteCode.expires_at.is_(None)) | (InviteCode.expires_at > func.now()),
            InviteCode.uses < InviteCode.max_uses,
        )
        .values(uses=InviteCode.uses + 1)
        .returning(InviteCode.id)
    )
    code_id = redeemed.scalar_one_or_none()
    if code_id is None:
        await db.rollback()
        raise SignupRefused("code")
    return code_id
