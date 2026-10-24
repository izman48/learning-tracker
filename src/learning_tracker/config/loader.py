"""Load and check the settings for one service from LT_* environment variables.

- An unknown LT_* name stops startup, with a "did you mean" hint for typos.
- A setting owned by another service stops startup (a secret in the wrong container).
- A value still showing a .env.example `<placeholder>` stops startup.
- An empty value counts as unset, because Compose interpolates a missing variable as "".
- Every problem is reported at once, and no message contains a secret value.
"""

import difflib
import re
from collections.abc import Mapping, Sequence
from datetime import time
from typing import Any
from urllib.parse import quote, urlsplit

from learning_tracker.config.catalogue import BY_NAME, SERVICES, SETTINGS, Setting, Settings
from learning_tracker.config.values import LOCAL_HOSTS, Secret

PLACEHOLDER = re.compile(r"<[^<>]+>")  # how .env.example marks values to fill in


class ConfigError(Exception):
    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


def load_settings(service: str, environ: Mapping[str, str]) -> Settings:
    if service not in SERVICES:
        raise ValueError(f"unknown service {service!r}; expected one of {', '.join(SERVICES)}")
    given = {k: val for k, val in environ.items() if k.startswith("LT_") and val != ""}
    problems = _ownership_problems(service, given)
    values: dict[str, Any] = {}
    for setting in SETTINGS:
        values[setting.field] = _value(service, setting, given, problems)
    if service in ("web", "worker") and values["database_url"] is None and values["db_password"]:
        password = quote(values["db_password"].reveal(), safe="")
        values["database_url"] = Secret(f"postgresql+psycopg://lt:{password}@db:5432/lt")
    problems += _cross_checks(service, given, values)
    if problems:
        raise ConfigError(problems)
    return Settings(service=service, **values)


def _ownership_problems(service: str, given: Mapping[str, str]) -> list[str]:
    problems = []
    for name in sorted(given):
        setting = BY_NAME.get(name)
        if setting is None:
            hint = difflib.get_close_matches(name, BY_NAME, n=1, cutoff=0.8)
            problems.append(
                f"unknown setting {name}" + (f"; did you mean {hint[0]}?" if hint else "")
            )
        elif service not in setting.services:
            problems.append(
                f"{name} must not be given to {service}"
                if setting.secret
                else f"{name} is not used by {service}"
            )
    return problems


def _value(service: str, setting: Setting, given: Mapping[str, str], problems: list[str]) -> Any:
    raw = given.get(setting.name) if service in setting.services else None
    if raw is None:
        if service in setting.required:
            problems.append(f"{setting.name} is required for {service}")
            return None
        raw = None if setting.secret else setting.default
    if raw is None:
        return None
    if PLACEHOLDER.fullmatch(raw):
        problems.append(f"{setting.name} is still a .env.example placeholder")
        return None
    try:
        return setting.parse(raw)
    except ValueError as exc:
        problems.append(f"{setting.name}{_shown(setting, raw)} {exc}")
        return None


def _shown(setting: Setting, raw: str) -> str:
    """The rejected value for the message: never for secrets, escaped if unprintable."""
    if setting.secret:
        return ""
    return f"={raw[:60]}" if raw.isprintable() else f"={raw[:60]!r}"


def _cross_checks(service: str, given: Mapping[str, str], values: Mapping[str, Any]) -> list[str]:
    problems = []
    origin = values["public_origin"]
    if values["debug"] and not (origin and urlsplit(origin).hostname in LOCAL_HOSTS):
        problems.append("LT_DEBUG=true is refused unless LT_PUBLIC_ORIGIN is a localhost origin")
    start, ready = values["pipeline_start"], values["list_ready_by"]
    if (
        service == "worker"
        and isinstance(start, time)
        and isinstance(ready, time)
        and start >= ready
    ):
        problems.append("LT_PIPELINE_START must be earlier than LT_LIST_READY_BY")
    if service == "worker" and values["smtp_host"] is not None:
        problems += [
            f"{name} is required when LT_SMTP_HOST is set"
            for name in ("LT_SMTP_FROM", "LT_SMTP_PASSWORD")
            if name not in given
        ]
    return problems
