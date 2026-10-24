"""Every LT_* setting, the services that read it, and the typed `Settings` result.

This is the single list of configuration. Each service accepts only the settings it
owns, so a secret handed to the wrong container stops it at startup. "compose"
settings are read by docker compose or the backup script, never by Python, so any
Python service that is given one refuses to start.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from typing import Any

from learning_tracker.config import values as v
from learning_tracker.config.values import Secret
from learning_tracker.keys import Keys

SERVICES = ("web", "worker", "fetcher")
WEB, WORKER, FETCHER, COMPOSE = "web", "worker", "fetcher", "compose"
_WW = (WEB, WORKER)
_REPO_URL = "https://github.com/izman48/learning-tracker"
_MODEL = "claude-haiku-5-5"


@dataclass(frozen=True)
class Setting:
    name: str
    services: tuple[str, ...]
    parse: Callable[[str], Any]
    default: str | None = None  # raw text, parsed like a given value
    required: tuple[str, ...] = ()  # services that refuse to start without it
    secret: bool = False
    attr: str = ""  # Settings field; defaults to the name without LT_, lowercased

    @property
    def field(self) -> str:
        return self.attr or self.name.removeprefix("LT_").lower()


def _s(
    name: str,
    services: tuple[str, ...] | str,
    parse: Callable[[str], Any],
    default: str | None = None,
    **kw: Any,
) -> Setting:
    owners = (services,) if isinstance(services, str) else services
    return Setting(name, owners, parse, default, **kw)


SETTINGS: tuple[Setting, ...] = (
    _s("LT_SECRET_KEY", _WW, v.secret_key, required=_WW, secret=True, attr="keys"),
    _s("LT_DB_PASSWORD", _WW, v.secret, required=_WW, secret=True),
    _s("LT_DATABASE_URL", _WW, v.postgres_url, secret=True),
    _s("LT_PUBLIC_ORIGIN", _WW, v.origin, required=_WW),
    _s("LT_ANTHROPIC_API_KEY", WORKER, v.secret, required=(WORKER,), secret=True),
    _s("LT_INVITE_REQUIRED", WEB, v.boolean, "true"),
    _s("LT_MAX_USERS", WEB, v.integer(1, 100_000), "50"),
    _s("LT_SESSION_DAYS", WEB, v.integer(1, 90), "30"),
    _s("LT_MAX_TOPICS_PER_USER", WEB, v.integer(1, 10), "3"),
    _s("LT_TRUSTED_PROXY_IPS", WEB, v.proxy_ips, ""),
    _s("LT_BIND_ADDRESS", COMPOSE, v.ip, "127.0.0.1"),
    _s("LT_HTTP_PORT", COMPOSE, v.integer(1, 65535), "8080"),
    _s("LT_PUBLIC_HOSTNAME", COMPOSE, v.hostname),
    _s("LT_EDGE_SUBNET", COMPOSE, v.subnet, "172.31.251.0/24"),
    _s("LT_CADDY_IP", COMPOSE, v.ip, "172.31.251.10"),
    _s("LT_FETCH_EGRESS_SUBNET", COMPOSE, v.subnet, "172.31.250.0/24"),
    _s("LT_BACKUP_KEEP_DAYS", COMPOSE, v.integer(1, 365), "14"),
    _s("LT_DEBUG", SERVICES, v.boolean, "false"),
    _s("LT_LOG_LEVEL", SERVICES, v.one_of("debug", "info", "warning", "error"), "info"),
    _s("LT_MODEL_SEARCH", WORKER, v.model_id, _MODEL),
    _s("LT_MODEL_PROFILE", WORKER, v.model_id, _MODEL),
    _s("LT_MODEL_SCREEN", WORKER, v.model_id, _MODEL),
    _s("LT_MODEL_CHOOSE", WORKER, v.model_id, _MODEL),
    _s("LT_MODEL_EXPLAIN", WORKER, v.model_id, _MODEL),
    _s("LT_PRICE_OVERRIDES", WORKER, v.prices, "{}"),
    _s("LT_SPEND_CAP_INSTANCE_MONTHLY_USD", WORKER, v.usd, "10"),
    _s("LT_SPEND_CAP_USER_MONTHLY_USD", WORKER, v.usd, "1.50"),
    _s("LT_SPEND_CAP_USER_DAILY_USD", WORKER, v.usd, "0.15"),
    _s("LT_SEARCHES_PER_LIST_MAX", WORKER, v.integer(0, 4), "2"),
    _s("LT_SEARCH_RESULT_TOKEN_ALLOWANCE", WORKER, v.integer(5_000, 200_000), "15000"),
    _s("LT_SOURCE_ANTHROPIC_SEARCH", WORKER, v.boolean, "true"),
    _s("LT_SOURCE_HN", WORKER, v.boolean, "false"),
    _s("LT_DISCUSSION_HN", WORKER, v.boolean, "false"),
    _s("LT_PIPELINE_START", WORKER, v.hhmm, "02:00"),
    _s("LT_LIST_READY_BY", WORKER, v.hhmm, "06:00"),
    _s("LT_WORKER_CONCURRENCY", WORKER, v.integer(1, 8), "2"),
    _s("LT_RUN_BUDGET_SECONDS", WORKER, v.integer(60, 900), "480"),
    _s("LT_DOMAIN_BLOCKLIST_FILE", WORKER, v.absolute_path),
    _s("LT_RETENTION_DAYS_LISTS", WORKER, v.integer(30, 365), "90"),
    _s("LT_RETENTION_DAYS_RUNS", WORKER, v.integer(7, 365), "90"),
    _s("LT_FETCH_CONTACT_URL", FETCHER, v.https_url, _REPO_URL),
    _s("LT_SMTP_HOST", WORKER, v.hostname),
    _s("LT_SMTP_PORT", WORKER, v.port_of(587, 465), "587"),
    _s("LT_SMTP_TLS", WORKER, v.one_of("required"), "required"),
    _s("LT_SMTP_USER", WORKER, v.plain_text),
    _s("LT_SMTP_PASSWORD", WORKER, v.secret, secret=True),
    _s("LT_SMTP_FROM", WORKER, v.mail_from),
    _s("LT_RESET_TOKEN_MINUTES", _WW, v.integer(10, 60), "30"),
    _s("LT_CONFIRM_TOKEN_HOURS", _WW, v.integer(1, 72), "24"),
)
BY_NAME = {s.name: s for s in SETTINGS}


@dataclass(frozen=True, repr=False)
class Settings:
    """Effective settings for one service.

    A setting the service does not own keeps its default (or None for secrets and
    settings without a default), so code cannot read another service's value.
    """

    service: str
    keys: Keys | None
    db_password: Secret | None
    database_url: Secret | None
    public_origin: str | None
    anthropic_api_key: Secret | None
    invite_required: bool
    max_users: int
    session_days: int
    max_topics_per_user: int
    trusted_proxy_ips: tuple[str, ...]
    bind_address: str
    http_port: int
    public_hostname: str | None
    edge_subnet: str
    caddy_ip: str
    fetch_egress_subnet: str
    backup_keep_days: int
    debug: bool
    log_level: str
    model_search: str
    model_profile: str
    model_screen: str
    model_choose: str
    model_explain: str
    price_overrides: dict[str, dict[str, Decimal]]
    spend_cap_instance_monthly_usd: Decimal
    spend_cap_user_monthly_usd: Decimal
    spend_cap_user_daily_usd: Decimal
    searches_per_list_max: int
    search_result_token_allowance: int
    source_anthropic_search: bool
    source_hn: bool
    discussion_hn: bool
    pipeline_start: time
    list_ready_by: time
    worker_concurrency: int
    run_budget_seconds: int
    domain_blocklist_file: str | None
    retention_days_lists: int
    retention_days_runs: int
    fetch_contact_url: str
    smtp_host: str | None
    smtp_port: int
    smtp_tls: str
    smtp_user: str | None
    smtp_password: Secret | None
    smtp_from: str | None
    reset_token_minutes: int
    confirm_token_hours: int

    @property
    def mailer_enabled(self) -> bool:
        return self.smtp_host is not None

    def __repr__(self) -> str:
        return f"Settings(service={self.service!r})"
