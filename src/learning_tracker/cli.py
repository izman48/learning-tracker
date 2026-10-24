"""The `lt` command, and the startup guard every entrypoint calls.

lt check-config web|worker|fetcher   print effective settings, secrets masked
lt create-admin --email EMAIL        make the first admin (password asked twice)
lt admin grant|revoke --email EMAIL  give or take admin rights
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import time
from getpass import getpass

from learning_tracker.config import SETTINGS, ConfigError, Settings, load_settings
from learning_tracker.config.catalogue import SERVICES

EXIT_CONFIG = 2


def require_settings(service: str) -> Settings:
    """Load settings from the environment, or print every problem and exit non-zero."""
    try:
        return load_settings(service, os.environ)
    except ConfigError as exc:
        print(f"Configuration problems; {service} will not start:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG) from None


def render(settings: Settings) -> str:
    lines = []
    for setting in SETTINGS:
        if settings.service in setting.services:
            value = getattr(settings, setting.field)
            lines.append(f"{setting.name}={_show(value, secret=setting.secret)}")
    return "\n".join(lines)


def _show(value: object, *, secret: bool) -> str:
    if value is None:
        return "(unset)"
    if secret:
        return "***"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, tuple):
        return ",".join(value)
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, dict):
        return json.dumps(value, default=str, sort_keys=True)
    return str(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lt", description="learning-tracker admin commands")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-config", help="print effective settings, secrets masked")
    check.add_argument("service", choices=SERVICES)
    create = commands.add_parser("create-admin", help="create an admin account")
    create.add_argument("--email", required=True)
    admin = commands.add_parser("admin", help="grant or revoke admin rights")
    admin.add_argument("action", choices=("grant", "revoke"))
    admin.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    if args.command == "check-config":
        print(render(require_settings(args.service)))
        return 0
    return _admin_command(args)


def _admin_command(args: argparse.Namespace) -> int:
    # Imported here so `lt check-config` works without the database stack.
    from learning_tracker.admin_cli import AdminError, create_admin, set_admin

    settings = require_settings("web")
    url = settings.database_url.reveal() if settings.database_url else ""
    try:
        if args.command == "create-admin":
            # Never from argv: arguments show up in shell history and process lists.
            password = getpass("New admin password: ")
            if getpass("Repeat it: ") != password:
                print("The passwords don't match.", file=sys.stderr)
                return 1
            create_admin(url, args.email, password)
            print("Admin account created.")
        else:
            set_admin(url, args.email, admin=args.action == "grant")
            print(f"Admin rights {'granted' if args.action == 'grant' else 'revoked'}.")
    except AdminError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
