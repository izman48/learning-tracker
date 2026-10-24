"""The web entrypoint loads settings once and refuses to start on bad config."""

import base64
from datetime import timedelta

import pytest
from fastapi import FastAPI

from learning_tracker.config import load_settings
from learning_tracker.web.config import WebConfig
from learning_tracker.web.main import app

ENV = {
    "LT_SECRET_KEY": base64.b64encode(bytes(range(48))).decode(),
    "LT_DB_PASSWORD": "pw for tests",
    "LT_PUBLIC_ORIGIN": "https://reads.example.com",
    "LT_SESSION_DAYS": "7",
}


def test_web_config_comes_from_the_settings() -> None:
    settings = load_settings("web", ENV)

    config = WebConfig.from_settings(settings)

    assert config.session_key == settings.require_keys().session
    assert config.session_lifetime == timedelta(days=7)
    assert config.public_origin == "https://reads.example.com"
    assert config.database_url == "postgresql+psycopg://lt:pw%20for%20tests@db:5432/lt"
    assert "pw for tests" not in repr(config) and "%20" not in repr(config)


def test_app_factory_builds_the_app_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)

    assert isinstance(app(), FastAPI)


def test_app_factory_refuses_to_start_on_bad_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("LT_SECRET_KEY")

    with pytest.raises(SystemExit) as exit_info:
        app()

    assert exit_info.value.code == 2
    assert "LT_SECRET_KEY is required for web" in capsys.readouterr().err
