"""URL checks the fetcher runs before any DNS lookup, on the first URL and on every redirect hop.

Parsing uses yarl, the parser aiohttp itself uses, so the host we check is the host
the client connects to (no parser differential).
"""

import re
import socket
from dataclasses import dataclass
from ipaddress import ip_address

from yarl import URL

MAX_URL_LENGTH = 2048
MAX_HOST_LENGTH = 253
_PORT_FOR_SCHEME = {"http": 80, "https": 443}
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class UnsafeURL(ValueError):
    """The URL must not be fetched. The message is a short reason, safe to log."""


@dataclass(frozen=True)
class TargetURL:
    url: URL
    scheme: str
    host: str
    port: int


def validate_url(raw: str, *, base: str | None = None) -> TargetURL:
    if len(raw) > MAX_URL_LENGTH:
        raise UnsafeURL("url length")
    try:
        url = URL(raw)
        if base is not None:
            url = URL(base).join(url)
    except (ValueError, TypeError) as exc:
        raise UnsafeURL("malformed url") from exc

    scheme = (url.scheme or "").lower()
    if scheme not in _PORT_FOR_SCHEME:
        raise UnsafeURL("scheme not allowed")
    if url.user is not None or url.password is not None:
        raise UnsafeURL("userinfo not allowed")

    host = _check_host(url.raw_host)
    if url.port != _PORT_FOR_SCHEME[scheme]:
        raise UnsafeURL("port not allowed")
    return TargetURL(url=url.with_fragment(None), scheme=scheme, host=host, port=url.port)


def _check_host(raw_host: str | None) -> str:
    if not raw_host:
        raise UnsafeURL("missing host")
    host = raw_host.lower()
    bare = host[:-1] if host.endswith(".") else host
    if _is_ip_literal(bare):
        raise UnsafeURL("ip literal host")
    if len(bare) > MAX_HOST_LENGTH:
        raise UnsafeURL("host too long")
    labels = bare.split(".")
    if len(labels) < 2:
        raise UnsafeURL("single-label host")
    if not all(_LABEL.match(label) for label in labels):
        raise UnsafeURL("invalid host")
    return host


def _is_ip_literal(host: str) -> bool:
    try:
        ip_address(host.strip("[]"))
        return True
    except ValueError:
        pass
    try:
        socket.inet_aton(host)  # also accepts 127.1, 0x7f.1, 2130706433, octal parts
        return True
    except OSError:
        return False
