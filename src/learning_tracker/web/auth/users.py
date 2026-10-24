"""User storage for fastapi-users' UserManager.

fastapi-users-db-sqlalchemy pins SQLAlchemy below 2.1, so this is our own small
adapter implementing the same BaseUserDatabase protocol.

create and update accept only allow-listed fields, so no caller can set
is_superuser, is_active or is_verified through them; admin rights change only
through the host CLI. The store flushes; the caller commits.
"""

import uuid
from typing import Any

from fastapi_users.db import BaseUserDatabase
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from learning_tracker.db.models import User

CREATABLE = frozenset({"email", "hashed_password", "timezone"})
UPDATABLE = frozenset(
    {
        "hashed_password",
        "timezone",
        "timezone_changed_at",
        "password_changed_at",
        "password_changed_via",
        "email_confirmed_at",
    }
)


def _only(fields: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    refused = sorted(set(fields) - allowed)
    if refused:
        raise ValueError(f"fields not allowed: {', '.join(refused)}")
    return fields


# mypy cannot match SQLAlchemy's Mapped[...] attributes to fastapi-users' plain-attribute
# UserProtocol; at runtime User has every field the protocol needs.
class UserStore(BaseUserDatabase[User, uuid.UUID]):  # type: ignore[type-var]
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id: uuid.UUID) -> User | None:
        return await self.session.get(User, id)

    async def get_by_email(self, email: str) -> User | None:
        query = select(User).where(func.lower(User.email) == func.lower(email))
        return (await self.session.execute(query)).scalar_one_or_none()

    async def create(self, create_dict: dict[str, Any]) -> User:
        user = User(**_only(create_dict, CREATABLE))
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def update(self, user: User, update_dict: dict[str, Any]) -> User:
        for key, value in _only(update_dict, UPDATABLE).items():
            setattr(user, key, value)
        await self.session.flush()
        return user

    async def delete(self, user: User) -> None:
        await self.session.delete(user)
        await self.session.flush()
