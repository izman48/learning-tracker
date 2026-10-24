"""Our own auth routes. Request bodies accept only the fields listed here."""

import logging
from typing import Annotated
from zoneinfo import available_timezones

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func

from learning_tracker.db.models import PasswordChangeSource, User
from learning_tracker.web.auth.deps import (
    DB,
    client_ip,
    current_user,
    enforce,
    password_slot,
    rate_limiter,
    session_store,
    user_manager,
)
from learning_tracker.web.auth.manager import UserManager
from learning_tracker.web.auth.passwords import MAX_PASSWORD, MIN_PASSWORD
from learning_tracker.web.auth.sessions import COOKIE_NAME, SessionStore, cookie_transport
from learning_tracker.web.auth.signup import SignupRefused, create_account
from learning_tracker.web.config import WebConfig
from learning_tracker.web.ratelimit import (
    LOGIN_PER_IP,
    PASSWORD_PER_ACCOUNT,
    SIGNUP_PER_INSTANCE,
    SIGNUP_PER_IP,
    RateLimiter,
    account_subject,
)

log = logging.getLogger("learning_tracker.auth")
router = APIRouter(prefix="/api")

BAD_CREDENTIALS = "Email or password is wrong"
# Spec AUTH-2/4/7: the exact copy the PWA shows.
SIGNUP_REFUSALS = {
    "code": (400, "This code can't be used. Check it with the person who sent it."),
    "exists": (400, "We couldn't create your account. If you already have one, sign in instead."),
    "closed": (403, "This app isn't taking new accounts right now."),
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class PasswordChangeIn(Strict):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    new_password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)


def _known_timezone(value: str) -> str:
    if value not in available_timezones():
        raise ValueError("unknown time zone")
    return value


class SignupIn(Strict):
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)
    timezone: Annotated[str, AfterValidator(_known_timezone)] = Field(max_length=64)
    code: str | None = Field(default=None, max_length=64)


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
    found = await manager.user_db.get_by_email(body.email)
    await enforce(
        limiter, PASSWORD_PER_ACCOUNT, account_subject(found.id if found else None, body.email)
    )
    async with password_slot(request):
        matches = await to_thread.run_sync(manager.password_matches, found, body.password)
    user = found if matches else None
    if user is None:
        log.info("auth.login_failed")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_CREDENTIALS)
    token = await sessions.issue(user.id, replacing=request.cookies.get(COOKIE_NAME))
    await db.commit()
    log.info("auth.login user_id=%s", user.id)
    return await cookie_transport(sessions.lifetime).get_login_response(token)


@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupIn,
    request: Request,
    db: DB,
    manager: Manager,
    sessions: Sessions,
    limiter: Limiter,
) -> Response:
    await enforce(limiter, SIGNUP_PER_IP, client_ip(request))
    await enforce(limiter, SIGNUP_PER_INSTANCE, "instance")
    config: WebConfig = request.app.state.config
    async with password_slot(request):  # hashed before the sign-up lock is taken
        hashed = await to_thread.run_sync(manager.password_helper.hash, body.password)
    try:
        user_id = await create_account(
            db,
            config,
            email=str(body.email),
            hashed_password=hashed,
            timezone=body.timezone,
            code=body.code,
        )
    except SignupRefused as refused:
        log.info("auth.signup_refused reason=%s", refused.reason)
        code, detail = SIGNUP_REFUSALS[refused.reason]
        raise HTTPException(code, detail) from None
    token = await sessions.issue(user_id, replacing=request.cookies.get(COOKIE_NAME))
    await db.commit()
    log.info("auth.signup user_id=%s", user_id)
    response = await cookie_transport(sessions.lifetime).get_login_response(token)
    response.status_code = status.HTTP_201_CREATED
    return response


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
    request: Request,
    db: DB,
    user: CurrentUser,
    manager: Manager,
    sessions: Sessions,
    limiter: Limiter,
) -> Response:
    # Shares the sign-in bucket, so a stolen session can't brute-force the password.
    await enforce(limiter, PASSWORD_PER_ACCOUNT, account_subject(user.id, user.email))
    async with password_slot(request):
        current_ok = await to_thread.run_sync(manager.check_password, user, body.current_password)
        new_hash = await to_thread.run_sync(manager.password_helper.hash, body.new_password)
    if not current_ok:
        log.info("auth.password_change_failed user_id=%s", user.id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong")
    await manager.user_db.update(
        user,
        {
            "hashed_password": new_hash,
            "password_changed_at": func.now(),  # the database's clock
            "password_changed_via": PasswordChangeSource.USER,
        },
    )
    # Every session ends, this device's too, and this device gets a fresh one, so
    # a cookie captured before the change stops working. One transaction.
    token = await sessions.restart_after_password_change(user.id)
    await db.commit()
    log.info("auth.password_changed user_id=%s", user.id)
    return await cookie_transport(sessions.lifetime).get_login_response(token)
