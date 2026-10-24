"""Admin: the people list and, without a mailer, single-use reset links.

The list shows only what the admin needs to run the instance: no topics, lists,
saved items or feedback.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import insert, select

from learning_tracker.db.models import AdminAudit, InviteCode, InviteRedemption, User
from learning_tracker.web.admin.deps import Admin
from learning_tracker.web.auth.deps import DB
from learning_tracker.web.auth.resets import issue_reset
from learning_tracker.web.config import WebConfig

router = APIRouter(prefix="/api/admin")


class PersonOut(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime
    is_admin: bool
    email_confirmed: bool
    invite_label: str | None


class ResetLinkOut(BaseModel):
    link: str
    expires_in_minutes: int


@router.get("/users")
async def list_people(admin: Admin, db: DB) -> list[PersonOut]:
    rows = (
        await db.execute(
            select(User, InviteCode.label)
            .outerjoin(InviteRedemption, InviteRedemption.user_id == User.id)
            .outerjoin(InviteCode, InviteCode.id == InviteRedemption.code_id)
            .order_by(User.created_at)
        )
    ).all()
    return [
        PersonOut(
            id=user.id,
            email=user.email,
            created_at=user.created_at,
            is_admin=user.is_superuser,
            email_confirmed=user.email_confirmed_at is not None,
            invite_label=label,
        )
        for user, label in rows
    ]


@router.post("/users/{user_id}/reset-link", status_code=status.HTTP_201_CREATED)
async def make_reset_link(
    user_id: uuid.UUID, request: Request, admin: Admin, db: DB
) -> ResetLinkOut:
    config: WebConfig = request.app.state.config
    if config.mailer_enabled:
        # One reset path per instance: with a mailer, people reset by email.
        raise HTTPException(status.HTTP_409_CONFLICT, "reset_by_email")
    if await db.get(User, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    token = await issue_reset(
        db, config.reset_key, user_id, minutes=config.reset_token_minutes, created_by=admin.id
    )
    await db.execute(
        insert(AdminAudit).values(
            actor_id=admin.id, action="user.reset_link", target_kind="user", target_id=str(user_id)
        )
    )
    await db.commit()
    # The admin hands this over; the origin comes from config, never the request.
    return ResetLinkOut(
        link=f"{config.public_origin}/reset#token={token}",
        expires_in_minutes=config.reset_token_minutes,
    )
