"""fastapi-users' UserManager, used only for authentication and password checks.

We mount none of fastapi-users' routers: its stock users router can set
is_superuser, and its create schema accepts is_active and is_verified.
"""

import uuid

from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import BaseUserManager, UUIDIDMixin

from learning_tracker.db.models import User
from learning_tracker.web.auth.passwords import password_helper
from learning_tracker.web.auth.users import UserStore


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):  # type: ignore[type-var]
    def __init__(self, store: UserStore) -> None:
        super().__init__(store, password_helper)

    async def sign_in(self, email: str, password: str) -> User | None:
        """The active user with this email and password, or None.

        Unknown emails still run the password hasher (fastapi-users does this), so
        timing does not reveal which emails have accounts.
        """
        user = await self.authenticate(OAuth2PasswordRequestForm(username=email, password=password))
        return user if user is not None and user.is_active else None

    def check_password(self, user: User, password: str) -> bool:
        verified, _ = self.password_helper.verify_and_update(password, user.hashed_password)
        return verified
