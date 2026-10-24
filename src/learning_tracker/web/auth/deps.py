"""Request-scoped dependencies: database session, stores, the signed-in user."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from learning_tracker.db.models import User
from learning_tracker.web.auth.manager import UserManager
from learning_tracker.web.auth.sessions import COOKIE_NAME, SessionStore
from learning_tracker.web.auth.users import UserStore
from learning_tracker.web.config import WebConfig


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


DB = Annotated[AsyncSession, Depends(db_session)]


def session_store(request: Request, db: DB) -> SessionStore:
    config: WebConfig = request.app.state.config
    return SessionStore(db, session_key=config.session_key, lifetime=config.session_lifetime)


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
