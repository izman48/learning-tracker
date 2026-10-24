"""The admin gate: anyone who is not a signed-in admin gets 404, so admin routes
cannot even be discovered by probing."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from learning_tracker.db.models import User
from learning_tracker.web.auth.deps import DB, session_store
from learning_tracker.web.auth.sessions import COOKIE_NAME, SessionStore


async def current_admin(
    request: Request, db: DB, sessions: Annotated[SessionStore, Depends(session_store)]
) -> User:
    user_id = await sessions.user_id_for(request.cookies.get(COOKIE_NAME))
    user = await db.get(User, user_id) if user_id else None
    if user is None or not user.is_superuser:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return user


Admin = Annotated[User, Depends(current_admin)]
