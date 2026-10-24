"""Server-side sessions: the cookie carries a random token, the database keeps only
its HMAC under the session subkey, so a database read alone yields no usable session."""

import hashlib
import hmac
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from learning_tracker.db.engine import make_engine, make_session_factory
from learning_tracker.db.models import Session, User
from learning_tracker.web.auth.sessions import (
    COOKIE_NAME,
    SessionStore,
    cookie_transport,
)
from learning_tracker.web.auth.users import UserStore

pytestmark = pytest.mark.anyio

SESSION_KEY = b"k" * 32
OTHER_KEY = b"o" * 32


@pytest.fixture
async def db(migrated_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = make_engine(migrated_database_url)
    async with make_session_factory(engine)() as session:
        yield session
    await engine.dispose()


async def add_user(db: AsyncSession, email: str = "reader@example.com") -> User:
    user = (
        await db.execute(
            insert(User).values(email=email, hashed_password="x", timezone="UTC").returning(User)
        )
    ).scalar_one()
    await db.commit()
    return user


def store(db: AsyncSession, key: bytes = SESSION_KEY, days: int = 30) -> SessionStore:
    return SessionStore(db, session_key=key, lifetime=timedelta(days=days))


async def session_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(Session))).scalar_one()


async def test_issued_token_reads_back_as_its_user(db: AsyncSession) -> None:
    user = await add_user(db)
    token = await store(db).issue(user.id)

    assert await store(db).user_id_for(token) == user.id


async def test_database_holds_only_the_hmac_of_the_token(db: AsyncSession) -> None:
    user = await add_user(db)
    token = await store(db).issue(user.id)

    stored = (await db.execute(select(Session.token_hash))).scalar_one()
    assert stored == hmac.new(SESSION_KEY, token.encode(), hashlib.sha256).digest()
    assert token.encode() not in stored
    assert stored != hashlib.sha256(token.encode()).digest()  # keyed, not a plain hash


async def test_tokens_are_long_random_and_unique(db: AsyncSession) -> None:
    user = await add_user(db)
    tokens = {await store(db).issue(user.id) for _ in range(20)}

    assert len(tokens) == 20
    assert all(len(t) >= 43 for t in tokens)  # 32 random bytes, base64url


async def test_stored_hash_is_useless_as_a_cookie(db: AsyncSession) -> None:
    user = await add_user(db)
    await store(db).issue(user.id)
    stored = (await db.execute(select(Session.token_hash))).scalar_one()

    assert await store(db).user_id_for(stored.hex()) is None


async def test_token_is_bound_to_the_session_key(db: AsyncSession) -> None:
    user = await add_user(db)
    token = await store(db).issue(user.id)

    assert await store(db, key=OTHER_KEY).user_id_for(token) is None


@pytest.mark.parametrize("token", [None, "", "x" * 200, "not ascii é", "garbage"])
async def test_missing_or_malformed_tokens_read_as_nobody(
    db: AsyncSession, token: str | None
) -> None:
    await add_user(db)

    assert await store(db).user_id_for(token) is None


async def test_expired_session_reads_as_nobody(db: AsyncSession) -> None:
    user = await add_user(db)
    token = await store(db).issue(user.id)
    await db.execute(update(Session).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    await db.commit()

    assert await store(db).user_id_for(token) is None


async def test_inactive_user_reads_as_nobody(db: AsyncSession) -> None:
    user = await add_user(db)
    token = await store(db).issue(user.id)
    await db.execute(update(User).values(is_active=False))
    await db.commit()

    assert await store(db).user_id_for(token) is None


async def test_lifetime_comes_from_settings(db: AsyncSession) -> None:
    user = await add_user(db)
    before = datetime.now(UTC)
    await store(db, days=7).issue(user.id)
    after = datetime.now(UTC)

    expires = (await db.execute(select(Session.expires_at))).scalar_one()
    assert before + timedelta(days=7) <= expires <= after + timedelta(days=7)


async def test_login_rotates_the_presented_session(db: AsyncSession) -> None:
    user = await add_user(db)
    old = await store(db).issue(user.id)

    new = await store(db).issue(user.id, replacing=old)

    assert new != old
    assert await store(db).user_id_for(old) is None
    assert await store(db).user_id_for(new) == user.id


async def test_rotation_never_deletes_another_users_session(db: AsyncSession) -> None:
    alice = await add_user(db, "alice@example.com")
    bob = await add_user(db, "bob@example.com")
    bobs = await store(db).issue(bob.id)

    await store(db).issue(alice.id, replacing=bobs)

    assert await store(db).user_id_for(bobs) == bob.id


async def test_revoke_deletes_one_session(db: AsyncSession) -> None:
    user = await add_user(db)
    a = await store(db).issue(user.id)
    b = await store(db).issue(user.id)

    await store(db).revoke(a)

    assert await store(db).user_id_for(a) is None
    assert await store(db).user_id_for(b) == user.id


async def test_password_change_ends_every_other_session(db: AsyncSession) -> None:
    alice = await add_user(db, "alice@example.com")
    bob = await add_user(db, "bob@example.com")
    current = await store(db).issue(alice.id)
    other = await store(db).issue(alice.id)
    bobs = await store(db).issue(bob.id)

    await store(db).revoke_others(alice.id, keep=current)

    assert await store(db).user_id_for(current) == alice.id
    assert await store(db).user_id_for(other) is None
    assert await store(db).user_id_for(bobs) == bob.id


async def test_revoke_all_ends_every_session_of_the_user(db: AsyncSession) -> None:
    alice = await add_user(db, "alice@example.com")
    bob = await add_user(db, "bob@example.com")
    for _ in range(3):
        await store(db).issue(alice.id)
    bobs = await store(db).issue(bob.id)

    await store(db).revoke_all(alice.id)

    assert await session_count(db) == 1
    assert await store(db).user_id_for(bobs) == bob.id


async def test_purge_removes_only_expired_sessions(db: AsyncSession) -> None:
    user = await add_user(db)
    live = await store(db).issue(user.id)
    await store(db).issue(user.id)
    await db.execute(
        update(Session)
        .where(Session.token_hash != store(db).hash(live))
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await db.commit()

    assert await store(db).purge_expired() == 1
    assert await session_count(db) == 1


async def test_user_store_finds_users_by_email_ignoring_case(db: AsyncSession) -> None:
    user = await add_user(db, "Reader@Example.com")

    found = await UserStore(db).get_by_email("reader@example.COM")

    assert found is not None and found.id == user.id


def test_cookie_is_host_only_secure_httponly_lax() -> None:
    transport = cookie_transport(timedelta(days=30))

    assert COOKIE_NAME == "__Host-lt_session"
    assert transport.cookie_name == COOKIE_NAME
    assert transport.cookie_secure is True
    assert transport.cookie_httponly is True
    assert transport.cookie_samesite == "lax"
    assert transport.cookie_path == "/"
    assert transport.cookie_domain is None  # __Host- forbids Domain
    assert transport.cookie_max_age == 30 * 24 * 3600


async def test_login_cookie_header_has_every_flag() -> None:
    response = await cookie_transport(timedelta(days=30)).get_login_response("tok")
    header = response.headers["set-cookie"]

    assert header.startswith("__Host-lt_session=tok;")
    for part in ("HttpOnly", "Path=/", "SameSite=lax", "Secure", "Max-Age=2592000"):
        assert part in header
    assert "Domain" not in header
