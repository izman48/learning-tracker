"""Admin rights from the host only.

There is no default admin account and no API that grants admin rights: someone with
shell access to the host runs `lt create-admin` or `lt admin grant|revoke`. Every
change is recorded in admin_audit (actor empty: the host).
"""

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import Connection, create_engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from learning_tracker.db.models import AdminAudit, User
from learning_tracker.web.auth.passwords import MAX_PASSWORD, MIN_PASSWORD, password_helper


class AdminError(Exception):
    """A refusal with a message safe to print."""


def create_admin(database_url: str, email: str, password: str) -> None:
    _check_email(email)
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AdminError(f"use a password of {MIN_PASSWORD} to {MAX_PASSWORD} characters")
    hashed = password_helper.hash(password)
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            user_id = conn.execute(
                insert(User)
                .values(email=email, hashed_password=hashed, timezone="UTC", is_superuser=True)
                .returning(User.id)
            ).scalar_one()
            _audit(conn, "admin.create", user_id)
    except IntegrityError:
        raise AdminError("an account with that email exists; use `lt admin grant`") from None
    finally:
        engine.dispose()


def set_admin(database_url: str, email: str, *, admin: bool) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            if not admin:
                # Lock the admin rows, so two revokes can't both pass this check.
                admins = (
                    conn.execute(
                        select(User.email).where(User.is_superuser.is_(True)).with_for_update()
                    )
                    .scalars()
                    .all()
                )
                if [e.lower() for e in admins] == [email.lower()]:
                    raise AdminError("that is the last admin; grant another account first")
            user_id = conn.execute(
                update(User)
                .where(func.lower(User.email) == func.lower(email))
                .values(is_superuser=admin)
                .returning(User.id)
            ).scalar_one_or_none()
            if user_id is None:
                raise AdminError("no account with that email")
            _audit(conn, "admin.grant" if admin else "admin.revoke", user_id)
    finally:
        engine.dispose()


def _check_email(email: str) -> None:
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        raise AdminError("that is not an email address") from None


def _audit(conn: Connection, action: str, user_id: object) -> None:
    conn.execute(
        insert(AdminAudit).values(action=action, target_kind="user", target_id=str(user_id))
    )
