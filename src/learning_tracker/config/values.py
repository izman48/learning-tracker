"""Strict parsers for LT_* setting values, and the `Secret` wrapper.

Each parser takes the raw environment string and returns the typed value, or raises
`ValueError` with a short reason. Reasons never include the raw value, so a parser
can be used for secrets and its message logged safely.
"""

import base64
import binascii
import json
import re
from collections.abc import Callable
from datetime import time
from decimal import Decimal, InvalidOperation
from email.utils import getaddresses
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from learning_tracker.keys import MIN_SECRET_BYTES, Keys, derive_keys

LOCAL_HOSTS = ("localhost", "127.0.0.1")
MIN_DISTINCT_BYTES = 16


class Secret:
    """A secret string whose repr and str never show the value."""

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    __str__ = __repr__


def boolean(raw: str) -> bool:
    if raw not in ("true", "false"):
        raise ValueError("must be exactly true or false")
    return raw == "true"


def integer(lo: int, hi: int) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        if not re.fullmatch(r"[0-9]{1,9}", raw):
            raise ValueError("must be a whole number")
        if not lo <= int(raw) <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return int(raw)

    return parse


def one_of(*options: str) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        if raw not in options:
            raise ValueError(f"must be one of {', '.join(options)}")
        return raw

    return parse


def port_of(*ports: int) -> Callable[[str], int]:
    choose = one_of(*(str(p) for p in ports))
    return lambda raw: int(choose(raw))


def usd(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError("must be a number of US dollars") from None
    if not value.is_finite() or not 0 <= value <= 100_000:
        raise ValueError("must be between 0 and 100000")
    return value


def hhmm(raw: str) -> time:
    if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", raw):
        raise ValueError("must be HH:MM (24-hour)")
    return time(int(raw[:2]), int(raw[3:]))


def secret_key(raw: str) -> Keys:
    try:
        decoded = base64.b64decode(raw, validate=True)
    except binascii.Error, ValueError:
        raise ValueError("must be base64 (openssl rand -base64 48)") from None
    if len(decoded) < MIN_SECRET_BYTES:
        raise ValueError(f"must decode to at least {MIN_SECRET_BYTES} bytes")
    # 32 random bytes hold about 30 distinct values; a typed or repeated key holds few.
    if len(set(decoded)) < MIN_DISTINCT_BYTES:
        raise ValueError("has too little randomness; generate one with openssl rand -base64 48")
    return derive_keys(decoded)


def secret(raw: str) -> Secret:
    return Secret(raw)


def postgres_url(raw: str) -> Secret:
    if re.search(r"\s", raw) or not raw.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("must be a postgresql+psycopg:// URL")
    return Secret(raw)


def origin(raw: str) -> str:
    if re.search(r"\s", raw):
        raise ValueError("must not contain whitespace")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must look like https://reads.example.com")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("must be an origin only: scheme, host and optional port")
    if parts.path not in ("", "/"):
        raise ValueError("must be an origin only: scheme, host and optional port")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        raise ValueError("must use https:// unless the host is localhost or 127.0.0.1")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("has an invalid port") from None
    if port == 0:
        raise ValueError("has an invalid port")
    # Normalised the way browsers send Origin, because the CSRF check compares exactly.
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    default = {"http": 80, "https": 443}[parts.scheme]
    return f"{parts.scheme}://{host}" + (f":{port}" if port not in (None, default) else "")


def ip(raw: str) -> str:
    try:
        return str(ip_address(raw))
    except ValueError:
        raise ValueError("must be an IP address") from None


def proxy_ips(raw: str) -> tuple[str, ...]:
    items = [item.strip() for item in raw.split(",") if item.strip()]
    if any(item == "*" or "/" in item for item in items):
        raise ValueError("lists single proxy IP addresses; wildcards and ranges are refused")
    return tuple(ip(item) for item in items)


def subnet(raw: str) -> str:
    try:
        return str(ip_network(raw))
    except ValueError:
        raise ValueError("must be a subnet such as 172.31.250.0/24") from None


_HOSTNAME = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def hostname(raw: str) -> str:
    if not _HOSTNAME.fullmatch(raw.lower()):
        raise ValueError("must be a hostname such as smtp.example.com")
    return raw.lower()


def model_id(raw: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]{1,99}", raw):
        raise ValueError("must be a model id such as claude-haiku-5-5")
    return raw


def https_url(raw: str) -> str:
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname or re.search(r"\s", raw):
        raise ValueError("must be an https:// URL")
    return raw


def absolute_path(raw: str) -> str:
    if not raw.startswith("/") or "\x00" in raw:
        raise ValueError("must be an absolute path inside the container")
    return raw


def plain_text(raw: str) -> str:
    if not raw.isprintable():
        raise ValueError("must not contain control characters")
    return raw


_ADDRESS = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


def mail_from(raw: str) -> str:
    if not raw.isprintable():
        raise ValueError("must not contain line breaks or control characters")
    addresses = getaddresses([raw])
    if len(addresses) != 1 or not _ADDRESS.fullmatch(addresses[0][1]):
        raise ValueError("must be one address, e.g. Reads <no-reply@reads.example.com>")
    return raw


def prices(raw: str) -> dict[str, dict[str, Decimal]]:
    try:
        data = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
    except json.JSONDecodeError:
        raise ValueError('must be JSON like {"model": {"in": 0.1, "out": 0.5}}') from None
    if not isinstance(data, dict):
        raise ValueError("must be a JSON object keyed by model id")
    out: dict[str, dict[str, Decimal]] = {}
    for model, price in data.items():
        model_id(model)
        if not isinstance(price, dict) or set(price) != {"in", "out"}:
            raise ValueError('each model needs exactly "in" and "out" (USD per million tokens)')
        if not all(isinstance(v, Decimal) and 0 <= v <= 1000 for v in price.values()):
            raise ValueError("prices must be numbers between 0 and 1000")
        out[model] = {"in": price["in"], "out": price["out"]}
    return out
