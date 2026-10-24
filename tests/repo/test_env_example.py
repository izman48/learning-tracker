"""T-70: .env.example ships placeholders only, never a usable secret."""

import re
from pathlib import Path

import pytest

SECRET_NAME = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN)")
PLACEHOLDER = re.compile(r"^<[^<>]+>$")
LOOKS_LIKE_A_REAL_SECRET = re.compile(r"(sk-[A-Za-z0-9-]{8,}|[A-Za-z0-9+/=_-]{32,})")


def parse_env_example(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip()
    return values


def assert_placeholders_only(values: dict[str, str]) -> None:
    for name, value in values.items():
        if PLACEHOLDER.match(value):
            continue
        assert not SECRET_NAME.search(name), f"{name} must be a <placeholder>"
        assert not LOOKS_LIKE_A_REAL_SECRET.search(value), f"{name} looks like a real secret"


def test_env_example_holds_placeholders_only(repo_root: Path) -> None:
    values = parse_env_example((repo_root / ".env.example").read_text())

    assert "LT_SECRET_KEY" in values  # precondition: the file declares the secrets
    assert_placeholders_only(values)


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
