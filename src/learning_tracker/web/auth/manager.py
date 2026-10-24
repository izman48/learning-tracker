"""fastapi-users' UserManager, used only for authentication and password checks.

We mount none of fastapi-users' routers: its stock users router can set
is_superuser, and its create schema accepts is_active and is_verified.
"""

import uuid

from fastapi_users import BaseUserManager, UUIDIDMixin

from learning_tracker.db.models import User
from learning_tracker.web.auth.passwords import password_helper
from learning_tracker.web.auth.users import UserStore


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):  # type: ignore[type-var]
    def __init__(self, store: UserStore) -> None:
        super().__init__(store, password_helper)

    def password_matches(self, user: User | None, password: str) -> bool:
        """Blocking: run it in a thread. An unknown user still costs one hash, so
        timing does not reveal which emails have accounts."""
        if user is None:
            self.password_helper.hash(password)
            return False
        return self.check_password(user, password) and user.is_active

    def check_password(self, user: User, password: str) -> bool:
        verified, _ = self.password_helper.verify_and_update(password, user.hashed_password)
        return verified
