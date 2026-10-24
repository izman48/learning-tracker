"""Admin invite codes: make (the plain code is shown once), list with state and who
used each one, and turn off. Every change is recorded in admin_audit, and every
time is taken from the database's clock."""

import uuid
from datetime import datetime, timedelta
from typing import Literal, cast

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, case, func, insert, select, update

from learning_tracker.db.models import AdminAudit, InviteCode, InviteRedemption, User
from learning_tracker.web.admin.deps import Admin
from learning_tracker.web.auth.deps import DB
from learning_tracker.web.auth.invites import code_hash, new_code
from learning_tracker.web.config import WebConfig

router = APIRouter(prefix="/api/admin")

DEFAULT_EXPIRY_DAYS = 30  # codes expire unless the admin chooses otherwise


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=80)
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_in_days: int = Field(default=DEFAULT_EXPIRY_DAYS, ge=1, le=365)


class CodeMade(BaseModel):
    id: uuid.UUID
    code: str  # shown once; only its HMAC is stored
    label: str | None
    max_uses: int
    expires_at: datetime


class Redemption(BaseModel):
    email: str | None  # None: the account has since been deleted
    redeemed_at: datetime


State = Literal["active", "used_up", "expired", "off"]


class CodeOut(BaseModel):
    id: uuid.UUID
    label: str | None
    uses: int
    max_uses: int
    state: State
    created_at: datetime
    expires_at: datetime | None
    redeemed_by: list[Redemption]


def _state() -> ColumnElement[str]:
    return case(
        (InviteCode.disabled_at.is_not(None), "off"),
        (InviteCode.expires_at <= func.now(), "expired"),
        (InviteCode.uses >= InviteCode.max_uses, "used_up"),
        else_="active",
    )


async def _audit(db: DB, admin: Admin, action: str, code_id: uuid.UUID) -> None:
    await db.execute(
        insert(AdminAudit).values(
            actor_id=admin.id, action=action, target_kind="invite_code", target_id=str(code_id)
        )
    )


@router.post("/codes", status_code=status.HTTP_201_CREATED)
async def make_code(body: CodeIn, request: Request, admin: Admin, db: DB) -> CodeMade:
    config: WebConfig = request.app.state.config
    code = new_code()
    code_id, expires_at = (
        await db.execute(
            insert(InviteCode)
            .values(
                code_hash=code_hash(config.invite_key, code),
                label=body.label,
                max_uses=body.max_uses,
                expires_at=func.now() + timedelta(days=body.expires_in_days),
                created_by=admin.id,
            )
            .returning(InviteCode.id, InviteCode.expires_at)
        )
    ).one()
    await _audit(db, admin, "code.create", code_id)
    await db.commit()
    return CodeMade(
        id=code_id,
        code=code,
        label=body.label,
        max_uses=body.max_uses,
        expires_at=cast(datetime, expires_at),
    )


@router.get("/codes")
async def list_codes(admin: Admin, db: DB) -> list[CodeOut]:
    codes = (
        await db.execute(
            select(InviteCode, _state().label("state")).order_by(InviteCode.created_at.desc())
        )
    ).all()
    redemptions = (
        await db.execute(
            select(InviteRedemption.code_id, User.email, InviteRedemption.redeemed_at)
            .outerjoin(User, User.id == InviteRedemption.user_id)
            .order_by(InviteRedemption.redeemed_at)
        )
    ).all()
    by_code: dict[uuid.UUID, list[Redemption]] = {}
    for code_id, email, redeemed_at in redemptions:
        by_code.setdefault(code_id, []).append(Redemption(email=email, redeemed_at=redeemed_at))
    return [
        CodeOut(
            id=code.id,
            label=code.label,
            uses=code.uses,
            max_uses=code.max_uses,
            state=cast(State, state),
            created_at=code.created_at,
            expires_at=code.expires_at,
            redeemed_by=by_code.get(code.id, []),
        )
        for code, state in codes
    ]


@router.post("/codes/{code_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_code(code_id: uuid.UUID, admin: Admin, db: DB) -> Response:
    found = (
        await db.execute(
            update(InviteCode)
            .where(InviteCode.id == code_id)
            .values(disabled_at=func.coalesce(InviteCode.disabled_at, func.now()))
            .returning(InviteCode.id)
        )
    ).scalar_one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    await _audit(db, admin, "code.disable", code_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
