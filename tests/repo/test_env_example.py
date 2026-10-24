""".env.example lists every setting once, ships placeholders for secrets (T-70), and
documents the same defaults the code uses."""

import base64
import re
from pathlib import Path

import pytest

from learning_tracker.config import PLACEHOLDER, SETTINGS, load_settings

LOOKS_LIKE_A_REAL_SECRET = re.compile(r"(sk-[A-Za-z0-9-]{8,}|[A-Za-z0-9+/=_-]{32,})")
SECRETS = {s.name for s in SETTINGS if s.secret}
# Live lines are only what an operator must fill in (LT_DB_PASSWORD is required unless an
# external LT_DATABASE_URL is given). Everything else is a commented default, so a copied
# .env does not freeze today's defaults when a later release tightens them.
LIVE = {s.name for s in SETTINGS if s.required} | {"LT_DB_PASSWORD"}
COMMENTED_SETTING = re.compile(r"#\s*(LT_[A-Z0-9_]+)=(.*)")


def commented_settings(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        if match := COMMENTED_SETTING.fullmatch(line.strip()):
            assert match[1] not in found, f"{match[1]} is listed twice"
            found[match[1]] = match[2].strip()
    return found


def parse_env_example(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, _, value = stripped.partition("=")
        assert name.strip() not in values, f"{name} is listed twice"
        values[name.strip()] = value.strip()
    return values


def assert_placeholders_only(values: dict[str, str]) -> None:
    for name, value in values.items():
        if not value or PLACEHOLDER.fullmatch(value):
            continue
        assert name not in SECRETS, f"{name} must be a <placeholder> or empty"
        assert not LOOKS_LIKE_A_REAL_SECRET.search(value), f"{name} looks like a real secret"


@pytest.fixture
def example_text(repo_root: Path) -> str:
    return (repo_root / ".env.example").read_text()


@pytest.fixture
def example(example_text: str) -> dict[str, str]:
    return parse_env_example(example_text)


@pytest.fixture
def documented(example_text: str) -> dict[str, str]:
    return commented_settings(example_text)


def test_only_the_required_settings_are_live(example: dict[str, str]) -> None:
    assert {"LT_SECRET_KEY", "LT_DB_PASSWORD", "LT_PUBLIC_ORIGIN", "LT_ANTHROPIC_API_KEY"} == LIVE
    assert set(example) == LIVE


def test_every_other_setting_is_documented_once_as_a_comment(
    example: dict[str, str], documented: dict[str, str]
) -> None:
    assert set(documented) == {s.name for s in SETTINGS} - LIVE
    assert not set(documented) & set(example)


def test_env_example_holds_placeholders_only(
    example: dict[str, str], documented: dict[str, str]
) -> None:
    assert "LT_SECRET_KEY" in SECRETS and "LT_SECRET_KEY" in example  # precondition
    assert_placeholders_only({**example, **documented})


def test_commented_defaults_are_the_defaults_the_code_uses(documented: dict[str, str]) -> None:
    for name, value in documented.items():
        default = next(s.default for s in SETTINGS if s.name == name)
        assert value == (default or ""), f"{name}: example {value!r} != default {default!r}"


@pytest.mark.parametrize("service", ["web", "worker", "fetcher"])
def test_env_example_loads_once_the_placeholders_are_filled(
    example: dict[str, str], service: str
) -> None:
    filled = {
        "LT_SECRET_KEY": base64.b64encode(bytes(range(48))).decode(),
        "LT_DB_PASSWORD": "pw-for-tests",
        "LT_PUBLIC_ORIGIN": "https://reads.example.com",
        "LT_ANTHROPIC_API_KEY": "key-for-tests",
    }
    owned = {s.name for s in SETTINGS if service in s.services}
    env = {k: filled.get(k, v) for k, v in example.items() if k in owned}
    assert load_settings(service, env).service == service


@pytest.mark.parametrize(
    "line",
    [
        "LT_SECRET_KEY=c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0",
        "LT_DB_PASSWORD=hunter2",
        "LT_ANTHROPIC_API_KEY=sk-ant-api03-abcdefgh",
        "LT_PUBLIC_ORIGIN=QWxhZGRpbjpvcGVuIHNlc2FtZUFsYWRkaW46b3Blbg",
    ],
)
def test_detector_fires_on_a_real_looking_value(line: str) -> None:
    with pytest.raises(AssertionError):
        assert_placeholders_only(parse_env_example(line))


def test_detector_allows_long_documented_defaults_for_non_secrets() -> None:
    assert_placeholders_only({"LT_RESET_TOKEN_MINUTES": "30", "LT_SMTP_TLS": "required"})


def test_configuration_guide_documents_every_setting(repo_root: Path) -> None:
    guide = (repo_root / "docs" / "configuration.md").read_text()
    assert "LT_SECRET_KEY" in guide  # precondition: the guide exists and is the right file
    missing = [s.name for s in SETTINGS if f"`{s.name}`" not in guide]
    assert missing == []
