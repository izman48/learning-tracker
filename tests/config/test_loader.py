"""Loading settings for one service from LT_* environment variables."""

import base64
import dataclasses
from decimal import Decimal

import pytest

from learning_tracker.config import SETTINGS, ConfigError, Settings, load_settings

GOOD_SECRET = base64.b64encode(bytes(range(48))).decode()
WEB = {
    "LT_SECRET_KEY": GOOD_SECRET,
    "LT_DB_PASSWORD": "db-pass-for-tests",
    "LT_PUBLIC_ORIGIN": "https://reads.example.com",
}
BASE: dict[str, dict[str, str]] = {
    "web": WEB,
    "worker": {**WEB, "LT_ANTHROPIC_API_KEY": "ai-key-for-tests"},
    "fetcher": {},
}
SMTP = {
    "LT_SMTP_HOST": "smtp.example.com",
    "LT_SMTP_FROM": "Reads <no-reply@reads.example.com>",
    "LT_SMTP_PASSWORD": "smtp-pass-for-tests",
}


def load(service: str, **overrides: str | None) -> Settings:
    env = dict(BASE[service])
    for name, value in overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    env["PATH"] = "/usr/bin"  # names without the LT_ prefix are ignored
    return load_settings(service, env)


def problems(service: str, **overrides: str | None) -> str:
    with pytest.raises(ConfigError) as info:
        load(service, **overrides)
    return str(info.value)


@pytest.mark.parametrize("service", ["web", "worker", "fetcher"])
def test_minimal_env_loads(service: str) -> None:
    assert load(service).service == service


def test_defaults_are_the_documented_safe_ones() -> None:
    web = load("web")
    assert web.invite_required is True and web.debug is False
    assert (web.max_users, web.session_days, web.trusted_proxy_ips) == (50, 30, ())
    worker = load("worker")
    assert worker.spend_cap_instance_monthly_usd == Decimal("10")
    assert worker.spend_cap_user_monthly_usd == Decimal("1.50")
    assert worker.spend_cap_user_daily_usd == Decimal("0.15")
    assert worker.search_result_token_allowance == 15000
    assert worker.source_hn is False and worker.discussion_hn is False
    assert worker.mailer_enabled is False
    assert (worker.reset_token_minutes, worker.confirm_token_hours) == (30, 24)


@pytest.mark.parametrize(
    "service,name",
    [
        ("web", "LT_SECRET_KEY"),
        ("web", "LT_DB_PASSWORD"),
        ("web", "LT_PUBLIC_ORIGIN"),
        ("worker", "LT_SECRET_KEY"),
        ("worker", "LT_DB_PASSWORD"),
        ("worker", "LT_PUBLIC_ORIGIN"),
        ("worker", "LT_ANTHROPIC_API_KEY"),
    ],
)
def test_missing_or_empty_required_variable_stops_startup(service: str, name: str) -> None:
    assert f"{name} is required for {service}" in problems(service, **{name: None})
    assert f"{name} is required for {service}" in problems(service, **{name: ""})


def test_empty_value_counts_as_unset_so_the_gate_stays_on() -> None:
    assert load("web", LT_INVITE_REQUIRED="").invite_required is True
    assert load("web", LT_INVITE_REQUIRED="false").invite_required is False


def test_unknown_variable_stops_startup_with_a_suggestion() -> None:
    msg = problems("web", LT_INVITE_REQURED="false")
    assert "unknown setting LT_INVITE_REQURED; did you mean LT_INVITE_REQUIRED?" in msg
    assert "unknown setting LT_ZZZ" in problems("web", LT_ZZZ="1")


@pytest.mark.parametrize(
    "service,name",
    [
        ("fetcher", "LT_SECRET_KEY"),
        ("fetcher", "LT_DB_PASSWORD"),
        ("fetcher", "LT_ANTHROPIC_API_KEY"),
        ("web", "LT_ANTHROPIC_API_KEY"),
        ("web", "LT_SMTP_PASSWORD"),
        ("fetcher", "LT_SMTP_PASSWORD"),
    ],
)
def test_a_secret_given_to_the_wrong_service_stops_startup(service: str, name: str) -> None:
    assert f"{name} must not be given to {service}" in problems(service, **{name: "x"})


def test_a_setting_of_another_service_is_refused() -> None:
    msg = problems("web", LT_SPEND_CAP_USER_DAILY_USD="0.15")
    assert "LT_SPEND_CAP_USER_DAILY_USD is not used by web" in msg


@pytest.mark.parametrize(
    "name,value",
    [
        ("LT_SECRET_KEY", "<generate-with-openssl-rand-base64-48>"),
        ("LT_PUBLIC_ORIGIN", "<https://reads.example.com>"),
        ("LT_DB_PASSWORD", "<choose-a-long-random-password>"),
    ],
)
def test_env_example_placeholders_are_refused(name: str, value: str) -> None:
    assert f"{name} is still a .env.example placeholder" in problems("web", **{name: value})


def test_a_bad_value_names_the_setting_and_the_reason() -> None:
    assert "LT_SESSION_DAYS=0 must be between 1 and 90" in problems("web", LT_SESSION_DAYS="0")
    assert "LT_INVITE_REQUIRED=yes must be exactly true or false" in problems(
        "web", LT_INVITE_REQUIRED="yes"
    )


def test_problems_never_echo_secret_values() -> None:
    weak = base64.b64encode(b"q" * 20).decode()
    msg = problems("worker", LT_SECRET_KEY=weak, LT_DATABASE_URL="mysql://u:hunter2@h/d")
    assert "LT_SECRET_KEY must decode to at least 32 bytes" in msg  # precondition
    assert weak not in msg and "hunter2" not in msg


def test_all_problems_are_reported_together() -> None:
    msg = problems("web", LT_SECRET_KEY=None, LT_SESSION_DAYS="0", LT_ZZZ="1")
    assert "LT_SECRET_KEY" in msg and "LT_SESSION_DAYS" in msg and "LT_ZZZ" in msg


def test_debug_is_refused_unless_the_origin_is_localhost() -> None:
    assert "LT_DEBUG=true is refused" in problems("web", LT_DEBUG="true")
    assert "LT_DEBUG=true is refused" in problems("fetcher", LT_DEBUG="true")
    local = load("web", LT_DEBUG="true", LT_PUBLIC_ORIGIN="http://localhost:8080")
    assert local.debug is True


def test_pipeline_must_start_before_the_list_is_due() -> None:
    msg = problems("worker", LT_PIPELINE_START="06:00", LT_LIST_READY_BY="05:00")
    assert "LT_PIPELINE_START must be earlier than LT_LIST_READY_BY" in msg


def test_mailer_loads_when_complete() -> None:
    s = load("worker", **SMTP)
    assert s.mailer_enabled is True
    assert (s.smtp_port, s.smtp_tls) == (587, "required")
    assert s.smtp_password is not None and s.smtp_password.reveal() == "smtp-pass-for-tests"


@pytest.mark.parametrize("missing", ["LT_SMTP_FROM", "LT_SMTP_PASSWORD"])
def test_mailer_host_makes_from_and_password_required(missing: str) -> None:
    msg = problems("worker", **{**SMTP, missing: None})
    assert f"{missing} is required when LT_SMTP_HOST is set" in msg


def test_web_reads_token_lifetimes_but_never_the_smtp_secret() -> None:
    s = load("web", LT_RESET_TOKEN_MINUTES="20", LT_CONFIRM_TOKEN_HOURS="48")
    assert (s.reset_token_minutes, s.confirm_token_hours) == (20, 48)
    assert s.smtp_password is None


@pytest.mark.parametrize("service", ["web", "worker"])
def test_database_url_is_built_from_the_password_when_not_given(service: str) -> None:
    s = load(service, LT_DB_PASSWORD="p@ss/word")
    assert s.database_url is not None
    assert s.database_url.reveal() == "postgresql+psycopg://lt:p%40ss%2Fword@db:5432/lt"


def test_explicit_database_url_wins_and_the_fetcher_has_none() -> None:
    url = "postgresql+psycopg://app:pw@db.example.com:5432/reads"
    s = load("web", LT_DATABASE_URL=url)
    assert s.database_url is not None and s.database_url.reveal() == url
    assert load("fetcher").database_url is None


def test_settings_hold_subkeys_and_never_the_raw_secret() -> None:
    s = load("web")
    assert s.keys is not None and len(s.keys.session) == 32
    assert "secret_key" not in {f.name for f in dataclasses.fields(Settings)}
    assert GOOD_SECRET not in repr(s) and "db-pass-for-tests" not in repr(s)


def test_settings_fields_match_the_catalogue_exactly() -> None:
    fields = {f.name for f in dataclasses.fields(Settings)} - {"service"}
    assert fields == {setting.field for setting in SETTINGS}


def test_catalogue_declares_each_variable_once_with_an_owner() -> None:
    names = [setting.name for setting in SETTINGS]
    assert len(names) == len(set(names)) == 49  # every row of the documented table
    assert all(setting.services for setting in SETTINGS)
    assert all(setting.name.startswith("LT_") for setting in SETTINGS)


def test_web_sees_that_a_mailer_is_configured_but_never_holds_its_secrets() -> None:
    # Web answers the auth options call and picks email vs admin reset from this flag.
    assert load("web").mailer_enabled is False  # precondition
    assert load("web", LT_SMTP_HOST="smtp.example.com").mailer_enabled is True
    for name in ("LT_SMTP_PASSWORD", "LT_SMTP_USER", "LT_SMTP_FROM"):
        msg = problems("web", LT_SMTP_HOST="smtp.example.com", **{name: "x"})
        assert f"{name} " in msg and "web" in msg
    assert "LT_SMTP_PASSWORD must not be given to web" in problems("web", LT_SMTP_PASSWORD="x")


def test_db_password_is_not_needed_when_an_external_database_url_is_given() -> None:
    url = "postgresql+psycopg://app:pw@db.example.com:5432/reads"
    s = load("web", LT_DB_PASSWORD=None, LT_DATABASE_URL=url)
    assert s.database_url is not None and s.database_url.reveal() == url
    assert "LT_DB_PASSWORD is required for web" in problems("web", LT_DB_PASSWORD=None)


def test_require_keys_returns_the_subkeys_or_fails_loudly() -> None:
    assert len(load("worker").require_keys().reset) == 32
    with pytest.raises(RuntimeError, match="fetcher has no keys"):
        load("fetcher").require_keys()
