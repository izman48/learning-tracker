"""Admin rights come only from the host CLI: no default admin, no API to grant it."""

import base64
from typing import Any

import pytest
from sqlalchemy import create_engine, select

from learning_tracker.admin_cli import AdminError, create_admin, set_admin
from learning_tracker.cli import main
from learning_tracker.db.models import AdminAudit, User
from learning_tracker.web.auth.passwords import password_helper

PASSWORD = "a long admin passphrase"
# Built at runtime, so no secret-shaped literal sits in this public repo.
FAKE_SECRET_KEY = base64.b64encode(bytes(range(48))).decode()


def rows(url: str, query: Any) -> list[Any]:
    engine = create_engine(url)
    with engine.connect() as conn:
        found = [tuple(row) for row in conn.execute(query).all()]
    engine.dispose()
    return found


def test_create_admin_makes_a_superuser_with_an_argon2_hash(migrated_database_url: str) -> None:
    create_admin(migrated_database_url, "Admin@example.com", PASSWORD)

    [(email, hashed, is_admin)] = rows(
        migrated_database_url, select(User.email, User.hashed_password, User.is_superuser)
    )
    assert email == "Admin@example.com" and is_admin is True
    assert (
        hashed.startswith("$argon2id$") and password_helper.verify_and_update(PASSWORD, hashed)[0]
    )
    assert [a for (a,) in rows(migrated_database_url, select(AdminAudit.action))] == [
        "admin.create"
    ]


@pytest.mark.parametrize(
    ("email", "password", "reason"),
    [
        ("admin@example.com", "short", "10"),
        ("not-an-email", PASSWORD, "email"),
        ("admin@example.com", "x" * 129, "128"),
    ],
)
def test_create_admin_refuses_bad_input(
    migrated_database_url: str, email: str, password: str, reason: str
) -> None:
    with pytest.raises(AdminError, match=reason):
        create_admin(migrated_database_url, email, password)


def test_create_admin_refuses_an_existing_email(migrated_database_url: str) -> None:
    create_admin(migrated_database_url, "admin@example.com", PASSWORD)

    with pytest.raises(AdminError, match="exists"):
        create_admin(migrated_database_url, "ADMIN@example.com", PASSWORD)


def test_grant_and_revoke_are_audited(migrated_database_url: str) -> None:
    create_admin(migrated_database_url, "keeper@example.com", PASSWORD)
    create_admin(migrated_database_url, "admin@example.com", PASSWORD)
    set_admin(migrated_database_url, "admin@example.com", admin=False)
    admin_flag = select(User.is_superuser).where(User.email == "admin@example.com")
    assert rows(migrated_database_url, admin_flag) == [(False,)]

    set_admin(migrated_database_url, "ADMIN@example.com", admin=True)

    assert rows(migrated_database_url, admin_flag) == [(True,)]
    actions = [
        a for (a,) in rows(migrated_database_url, select(AdminAudit.action).order_by(AdminAudit.id))
    ]
    assert actions == ["admin.create", "admin.create", "admin.revoke", "admin.grant"]


def test_grant_for_an_unknown_email_fails(migrated_database_url: str) -> None:
    with pytest.raises(AdminError, match="no account"):
        set_admin(migrated_database_url, "nobody@example.com", admin=True)


def test_cli_reads_the_password_from_the_terminal_never_argv(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in {
        "LT_SECRET_KEY": FAKE_SECRET_KEY,
        "LT_DATABASE_URL": migrated_database_url,
        "LT_PUBLIC_ORIGIN": "https://reads.example.com",
    }.items():
        monkeypatch.setenv(name, value)
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("learning_tracker.cli.getpass", lambda _prompt: next(answers))

    assert main(["create-admin", "--email", "admin@example.com"]) == 0
    assert rows(migrated_database_url, select(User.is_superuser)) == [(True,)]
    assert PASSWORD not in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main(["create-admin", "--email", "x@example.com", "--password", PASSWORD])


def test_cli_refuses_mismatched_passwords(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in {
        "LT_SECRET_KEY": FAKE_SECRET_KEY,
        "LT_DATABASE_URL": migrated_database_url,
        "LT_PUBLIC_ORIGIN": "https://reads.example.com",
    }.items():
        monkeypatch.setenv(name, value)
    answers = iter([PASSWORD, PASSWORD + "!"])
    monkeypatch.setattr("learning_tracker.cli.getpass", lambda _prompt: next(answers))

    assert main(["create-admin", "--email", "admin@example.com"]) == 1
    assert rows(migrated_database_url, select(User.id)) == []


def test_the_last_admin_cannot_be_revoked(migrated_database_url: str) -> None:
    create_admin(migrated_database_url, "admin@example.com", PASSWORD)

    with pytest.raises(AdminError, match="last admin"):
        set_admin(migrated_database_url, "admin@example.com", admin=False)

    assert rows(migrated_database_url, select(User.is_superuser)) == [(True,)]


def test_an_admin_can_be_revoked_while_another_remains(migrated_database_url: str) -> None:
    create_admin(migrated_database_url, "one@example.com", PASSWORD)
    create_admin(migrated_database_url, "two@example.com", PASSWORD)

    set_admin(migrated_database_url, "one@example.com", admin=False)

    flags = rows(migrated_database_url, select(User.email, User.is_superuser).order_by(User.email))
    assert flags == [("one@example.com", False), ("two@example.com", True)]
