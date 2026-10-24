"""SQLAlchemy models for the baseline schema: people, access and admin.

Rules every table follows:
- user-owned rows reference users.id with ON DELETE CASCADE, so deleting an
  account deletes its data; audit-style rows that must outlive a user use
  ON DELETE SET NULL instead (tests/db/test_user_data_cascade.py checks this);
- secrets are never stored: session, reset, confirmation tokens and invite codes
  are kept only as keyed hashes (HMAC under a purpose-specific key);
- times are timezone-aware (timestamptz).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint names, so migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

USER_FK = "users.id"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class PasswordChangeSource(enum.StrEnum):
    USER = "user"
    ADMIN_RESET = "admin_reset"
    EMAIL_RESET = "email_reset"


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("uq_users_email_lower", text("lower(email)"), unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    email: Mapped[str] = mapped_column(String(320))
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(server_default="true")
    is_superuser: Mapped[bool] = mapped_column(server_default="false")
    is_verified: Mapped[bool] = mapped_column(server_default="false")
    timezone: Mapped[str] = mapped_column(String(64))
    timezone_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_edition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_via: Mapped[PasswordChangeSource | None] = mapped_column(
        Enum(
            PasswordChangeSource,
            name="password_change_source",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    email_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
    next_topic_idx: Mapped[int] = mapped_column(server_default="0")


class Session(Base):
    """A signed-in browser. Only the HMAC of the cookie token is stored."""

    __tablename__ = "sessions"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK, ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PasswordReset(Base):
    """At most one live reset token per user: a new one replaces (revokes) the old."""

    __tablename__ = "password_resets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(USER_FK, ondelete="CASCADE"), primary_key=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailConfirmation(Base):
    """At most one live confirmation token per user."""

    __tablename__ = "email_confirmations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(USER_FK, ondelete="CASCADE"), primary_key=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InviteCode(Base):
    __tablename__ = "invite_codes"
    __table_args__ = (
        CheckConstraint("max_uses BETWEEN 1 AND 1000", name="max_uses_range"),
        CheckConstraint("uses >= 0 AND uses <= max_uses", name="uses_within_max"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    label: Mapped[str | None] = mapped_column(String(80))
    max_uses: Mapped[int] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_column()


class InviteRedemption(Base):
    """Who used which code. Survives account deletion as "deleted account"."""

    __tablename__ = "invite_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    code_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invite_codes.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete="SET NULL"))
    redeemed_at: Mapped[datetime] = created_at_column()


class AdminAudit(Base):
    """Append-only record of admin actions."""

    __tablename__ = "admin_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))
    target_kind: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()


class RateLimitBucket(Base):
    """Fixed-window counters. Keys hold HMACs of IPs and emails, never the raw values."""

    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)
