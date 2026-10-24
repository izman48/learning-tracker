"""Our own auth routes. Request bodies accept only the fields listed here."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from learning_tracker.db.models import PasswordChangeSource, User
from learning_tracker.web.auth.deps import (
    DB,
    client_ip,
    current_user,
    enforce,
    rate_limiter,
    session_store,
    user_manager,
)
from learning_tracker.web.auth.manager import UserManager
from learning_tracker.web.auth.passwords import MAX_PASSWORD, MIN_PASSWORD
from learning_tracker.web.auth.sessions import COOKIE_NAME, SessionStore, cookie_transport
from learning_tracker.web.ratelimit import LOGIN_PER_IP, PASSWORD_PER_ACCOUNT, RateLimiter

log = logging.getLogger("learning_tracker.auth")
router = APIRouter(prefix="/api")

BAD_CREDENTIALS = "Email or password is wrong"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class PasswordChangeIn(Strict):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    new_password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)


class MeOut(BaseModel):
    email: str
    timezone: str
    is_admin: bool
    email_confirmed: bool


Manager = Annotated[UserManager, Depends(user_manager)]
Sessions = Annotated[SessionStore, Depends(session_store)]
CurrentUser = Annotated[User, Depends(current_user)]
Limiter = Annotated[RateLimiter, Depends(rate_limiter)]


@router.post("/auth/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(
    body: LoginIn,
    request: Request,
    db: DB,
    manager: Manager,
    sessions: Sessions,
    limiter: Limiter,
) -> Response:
    # Counted before the password is checked: a limited caller costs no hashing.
    await enforce(limiter, LOGIN_PER_IP, client_ip(request))
    await enforce(limiter, PASSWORD_PER_ACCOUNT, body.email.lower())
    user = await manager.sign_in(body.email, body.password)
    if user is None:
        log.info("auth.login_failed")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_CREDENTIALS)
    token = await sessions.issue(user.id, replacing=request.cookies.get(COOKIE_NAME))
    await db.commit()
    log.info("auth.login user_id=%s", user.id)
    return await cookie_transport(sessions.lifetime).get_login_response(token)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, db: DB, sessions: Sessions) -> Response:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await sessions.revoke(token)
        await db.commit()
    log.info("auth.logout")
    response = await cookie_transport(sessions.lifetime).get_logout_response()
    # Ask the browser to drop cached lists and saved items from this device.
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
    return response


@router.get("/me")
async def me(user: CurrentUser) -> MeOut:
    return MeOut(
        email=user.email,
        timezone=user.timezone,
        is_admin=user.is_superuser,
        email_confirmed=user.email_confirmed_at is not None,
    )


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeIn,
    db: DB,
    user: CurrentUser,
    manager: Manager,
    sessions: Sessions,
    limiter: Limiter,
) -> Response:
    # Shares the sign-in bucket, so a stolen session can't brute-force the password.
    await enforce(limiter, PASSWORD_PER_ACCOUNT, user.email.lower())
    if not manager.check_password(user, body.current_password):
        log.info("auth.password_change_failed user_id=%s", user.id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong")
    await manager.user_db.update(
        user,
        {
            "hashed_password": manager.password_helper.hash(body.new_password),
            "password_changed_at": datetime.now(UTC),
            "password_changed_via": PasswordChangeSource.USER,
        },
    )
    # Every session ends, this device's too, and this device gets a fresh one, so
    # a cookie captured before the change stops working. One transaction.
    token = await sessions.restart_after_password_change(user.id)
    await db.commit()
    log.info("auth.password_changed user_id=%s", user.id)
    return await cookie_transport(sessions.lifetime).get_login_response(token)
