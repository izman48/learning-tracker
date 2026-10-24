"""What the web process needs from the settings, in one small typed object."""

from dataclasses import dataclass, field
from datetime import timedelta

from learning_tracker.config import Settings


@dataclass(frozen=True)
class WebConfig:
    database_url: str = field(repr=False)
    session_key: bytes = field(repr=False)
    session_lifetime: timedelta
    public_origin: str
    ip_key: bytes = field(repr=False)  # HMAC key for IPs and emails in rate-limit buckets
    invite_key: bytes = field(repr=False)  # HMAC key for stored invite codes
    trusted_proxy_ips: tuple[str, ...] = ()
    invite_required: bool = True
    max_users: int = 50

    @classmethod
    def from_settings(cls, settings: Settings) -> WebConfig:
        if settings.database_url is None or settings.public_origin is None:
            raise RuntimeError("web settings lack a database URL or public origin")
        return cls(
            database_url=settings.database_url.reveal(),
            session_key=settings.require_keys().session,
            session_lifetime=timedelta(days=settings.session_days),
            public_origin=settings.public_origin,
            ip_key=settings.require_keys().ip,
            invite_key=settings.require_keys().invite,
            trusted_proxy_ips=settings.trusted_proxy_ips,
            invite_required=settings.invite_required,
            max_users=settings.max_users,
        )
