"""Request-scoped dependencies: database session, stores, the signed-in user."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from learning_tracker.db.models import User
from learning_tracker.web.auth.manager import UserManager
from learning_tracker.web.auth.sessions import COOKIE_NAME, SessionStore
from learning_tracker.web.auth.users import UserStore
from learning_tracker.web.config import WebConfig
from learning_tracker.web.ratelimit import TOO_MANY, Limit, RateLimiter

log = logging.getLogger("learning_tracker.web")


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


DB = Annotated[AsyncSession, Depends(db_session)]


def session_store(request: Request, db: DB) -> SessionStore:
    config: WebConfig = request.app.state.config
    return SessionStore(db, session_key=config.session_key, lifetime=config.session_lifetime)


def rate_limiter(request: Request) -> RateLimiter:
    config: WebConfig = request.app.state.config
    return RateLimiter(request.app.state.session_factory, config.ip_key)


async def enforce(limiter: RateLimiter, limit: Limit, subject: str) -> None:
    retry_after = await limiter.hit(limit, subject)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TOO_MANY,
            headers={"Retry-After": str(retry_after)},
        )


PROXY_WARNING = (
    "requests arrive from a private address but LT_TRUSTED_PROXY_IPS is unset: behind a "
    "reverse proxy every caller shares one rate-limit budget; declare the proxy's address"
)
_PROXY_WARNING_EVERY = timedelta(hours=1)
# Where a reverse proxy or the Docker bridge usually sits.
_PROXY_NETWORKS = tuple(
    ip_network(net)
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
)


def client_ip(request: Request) -> str:
    """The caller's address. Behind a declared proxy, uvicorn has already replaced it
    with the forwarded one (--forwarded-allow-ips); we never read X-Forwarded-For."""
    ip = request.client.host if request.client else "unknown"
    _warn_if_undeclared_proxy(request, ip)
    return ip


def _warn_if_undeclared_proxy(request: Request, ip: str) -> None:
    config: WebConfig = request.app.state.config
    if config.trusted_proxy_ips:
        return
    try:
        address = ip_address(ip)
    except ValueError:
        return
    if not any(address in net for net in _PROXY_NETWORKS):
        return
    state = request.app.state
    now = datetime.now(UTC)
    last = getattr(state, "proxy_warning_at", None)
    state.proxy_warning = True  # read by the admin screen (B2)
    if last is None or now - last >= _PROXY_WARNING_EVERY:
        state.proxy_warning_at = now
        log.warning(PROXY_WARNING)


def user_manager(db: DB) -> UserManager:
    return UserManager(UserStore(db))


async def current_user(
    request: Request, db: DB, sessions: Annotated[SessionStore, Depends(session_store)]
) -> User:
    user_id = await sessions.user_id_for(request.cookies.get(COOKIE_NAME))
    user = await db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user
