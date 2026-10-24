"""`lt check-config` and the startup guard every entrypoint calls (fail closed, T-81)."""

import base64
import subprocess
import sys
from importlib.metadata import entry_points

import pytest

from learning_tracker.cli import require_settings

SECRET = base64.b64encode(bytes(range(48))).decode()
WORKER = {
    "LT_SECRET_KEY": SECRET,
    "LT_DB_PASSWORD": "db-pw-for-tests",
    "LT_PUBLIC_ORIGIN": "https://reads.example.com",
    "LT_ANTHROPIC_API_KEY": "ai-key-for-tests",
    "LT_SMTP_HOST": "smtp.example.com",
    "LT_SMTP_FROM": "Reads <no-reply@reads.example.com>",
    "LT_SMTP_PASSWORD": "smtp-pw-for-tests",
}
SECRET_VALUES = [SECRET, "db-pw-for-tests", "ai-key-for-tests", "smtp-pw-for-tests"]


def lt(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "learning_tracker.cli", *args],
        env={"PATH": "/usr/bin", **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_lt_is_installed_as_a_console_script() -> None:
    (script,) = entry_points(group="console_scripts", name="lt")
    assert script.value == "learning_tracker.cli:main"


def test_check_config_prints_the_effective_settings_with_secrets_masked() -> None:
    proc = lt("check-config", "worker", env=WORKER)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert "LT_SPEND_CAP_USER_DAILY_USD=0.15" in lines  # defaults shown
    assert "LT_SMTP_HOST=smtp.example.com" in lines
    for name in ("LT_SECRET_KEY", "LT_DB_PASSWORD", "LT_ANTHROPIC_API_KEY", "LT_SMTP_PASSWORD"):
        assert f"{name}=***" in lines
    assert "LT_DATABASE_URL=***" in lines  # built from the password, so masked too
    assert "LT_INVITE_REQUIRED" not in proc.stdout  # a web setting, not the worker's
    for value in SECRET_VALUES:
        assert value not in proc.stdout + proc.stderr


def test_bad_config_exits_non_zero_listing_every_problem_without_secrets() -> None:
    env = {**WORKER, "LT_PUBLIC_ORIGIN": "http://reads.example.com", "LT_SESSION_DAY": "3"}
    proc = lt("check-config", "worker", env=env)
    assert proc.returncode == 2
    assert "LT_PUBLIC_ORIGIN" in proc.stderr and "LT_SESSION_DAY" in proc.stderr
    assert "Traceback" not in proc.stderr
    for value in SECRET_VALUES:
        assert value not in proc.stdout + proc.stderr


def test_unknown_service_is_a_usage_error() -> None:
    proc = lt("check-config", "database", env=WORKER)
    assert proc.returncode == 2 and "invalid choice" in proc.stderr


def test_require_settings_returns_settings_when_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in WORKER.items():
        monkeypatch.setenv(name, value)
    assert require_settings("worker").mailer_enabled is True


def test_require_settings_stops_startup_on_bad_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LT_INVITE_REQURED", "false")
    with pytest.raises(SystemExit) as info:
        require_settings("web")
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "did you mean LT_INVITE_REQUIRED?" in err and "LT_SECRET_KEY is required" in err
