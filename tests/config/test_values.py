"""Strict parsing of LT_* values: each parser accepts only well-formed input."""

import base64
from collections.abc import Callable
from datetime import time
from decimal import Decimal
from typing import Any

import pytest

from learning_tracker.config import values as v


def refuses(parse: Callable[[str], Any], raw: str) -> str:
    with pytest.raises(ValueError) as info:
        parse(raw)
    return str(info.value)


def test_secret_never_shows_its_value() -> None:
    s = v.Secret("value-for-tests")
    assert s.reveal() == "value-for-tests"
    assert "value-for-tests" not in repr(s) and "value-for-tests" not in str(s)
    assert "value-for-tests" not in f"{s}"


@pytest.mark.parametrize("raw", ["yes", "no", "1", "0", "True", "FALSE", "on", " false"])
def test_booleans_accept_only_lowercase_true_or_false(raw: str) -> None:
    assert (v.boolean("true"), v.boolean("false")) == (True, False)
    refuses(v.boolean, raw)


@pytest.mark.parametrize("raw", ["0", "91", "3.5", "-1", "1e3", "", "12345678901"])
def test_integer_is_range_checked_and_digits_only(raw: str) -> None:
    parse = v.integer(1, 90)
    assert parse("90") == 90
    refuses(parse, raw)


def test_one_of_and_port_of() -> None:
    assert v.one_of("required")("required") == "required"
    refuses(v.one_of("required"), "none")
    assert v.port_of(587, 465)("465") == 465
    for bad in ("25", "2525", "587 "):
        refuses(v.port_of(587, 465), bad)


@pytest.mark.parametrize("raw", ["-1", "abc", "NaN", "Infinity", "100001"])
def test_usd_is_a_bounded_non_negative_amount(raw: str) -> None:
    assert v.usd("0") == 0 and v.usd("1.50") == Decimal("1.50")
    refuses(v.usd, raw)


@pytest.mark.parametrize("raw", ["25:00", "24:00", "6am", "6:00", "06:60"])
def test_hhmm(raw: str) -> None:
    assert v.hhmm("02:00") == time(2, 0) and v.hhmm("23:59") == time(23, 59)
    refuses(v.hhmm, raw)


def test_secret_key_yields_subkeys_and_refuses_weak_or_malformed_input() -> None:
    good = base64.b64encode(bytes(range(48))).decode()
    assert len(v.secret_key(good).session) == 32
    short = base64.b64encode(b"k" * 31).decode()
    for bad in (short, "not base64 !!", "a" * 10):
        reason = refuses(v.secret_key, bad)
        assert bad not in reason


@pytest.mark.parametrize(
    "raw",
    [
        "http://reads.example.com",
        "http://reads.example.com:8080",
        "ftp://reads.example.com",
        "https://reads.example.com/path",
        "https://reads.example.com?q=1",
        "https://reads.example.com#x",
        "reads.example.com",
        "https://user@reads.example.com",
        "https://",
        "https://reads.example.com\r\n",
    ],
)
def test_origin_must_be_a_bare_https_origin(raw: str) -> None:
    refuses(v.origin, raw)


@pytest.mark.parametrize(
    "raw", ["http://localhost:8080", "http://127.0.0.1:8080", "https://reads.example.com/"]
)
def test_origin_allows_http_only_on_localhost_and_drops_a_trailing_slash(raw: str) -> None:
    assert v.origin(raw) == raw.rstrip("/")


def test_postgres_url() -> None:
    url = "postgresql+psycopg://app:pw@db.example.com:5432/reads"
    assert v.postgres_url(url).reveal() == url
    for bad in ("sqlite:///x.db", "mysql://a:b@h/d", "postgresql://h/d\r\n"):
        assert bad not in refuses(v.postgres_url, bad)


def test_proxy_ips_are_single_addresses() -> None:
    assert v.proxy_ips("172.31.251.10, 2001:db8::5") == ("172.31.251.10", "2001:db8::5")
    assert v.proxy_ips("") == ()
    for bad in ("*", "0.0.0.0/0", "172.31.251.10,*"):
        assert "wildcards and ranges are refused" in refuses(v.proxy_ips, bad)
    refuses(v.proxy_ips, "proxy.example.com")


def test_ip_subnet_hostname_model_and_paths() -> None:
    assert v.ip("127.0.0.1") == "127.0.0.1"
    refuses(v.ip, "localhost")
    assert v.subnet("172.31.250.0/24") == "172.31.250.0/24"
    refuses(v.subnet, "172.31.250.1/24")
    assert v.hostname("SMTP.Example.com") == "smtp.example.com"
    for bad in ("smtp", "smtp..example.com", "-a.example.com", "a b.example.com"):
        refuses(v.hostname, bad)
    assert v.model_id("claude-haiku-5-5") == "claude-haiku-5-5"
    refuses(v.model_id, "Claude Haiku")
    assert v.https_url("https://example.com/repo") == "https://example.com/repo"
    refuses(v.https_url, "http://example.com/repo")
    assert v.absolute_path("/config/blocklist.txt") == "/config/blocklist.txt"
    refuses(v.absolute_path, "blocklist.txt")
    assert v.plain_text("apikey") == "apikey"
    refuses(v.plain_text, "user\r\nX: y")


@pytest.mark.parametrize(
    "raw",
    [
        "Reads <no-reply@reads.example.com>\r\nBcc: x@example.com",
        "a@example.com, b@example.com",
        "not an address",
        "Reads <no-reply@reads>",
    ],
)
def test_mail_from_is_one_address_without_line_breaks(raw: str) -> None:
    assert v.mail_from("Reads <no-reply@reads.example.com>")
    refuses(v.mail_from, raw)


def test_prices() -> None:
    assert v.prices('{"claude-haiku-5-5": {"in": 0.1, "out": 0.5}}') == {
        "claude-haiku-5-5": {"in": Decimal("0.1"), "out": Decimal("0.5")}
    }
    assert v.prices("{}") == {}
    for bad in (
        "{",
        "[]",
        '{"m-1": {"in": 1}}',
        '{"m-1": {"in": -1, "out": 1}}',
        '{"m-1": {"in": 1, "out": 1, "x": 2}}',
        '{"Bad Model": {"in": 1, "out": 1}}',
        '{"m-1": {"in": "1", "out": 1}}',
    ):
        refuses(v.prices, bad)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HTTPS://Reads.Example.COM", "https://reads.example.com"),
        ("https://reads.example.com:443", "https://reads.example.com"),
        ("https://reads.example.com:8443", "https://reads.example.com:8443"),
        ("http://LOCALHOST:80/", "http://localhost"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
    ],
)
def test_origin_is_normalised_as_browsers_send_it(raw: str, expected: str) -> None:
    # Compared byte for byte against the browser's Origin header by the CSRF check.
    assert v.origin(raw) == expected


@pytest.mark.parametrize(
    "raw", ["https://reads.example.com:99999", "https://reads.example.com:abc", "https://h:0"]
)
def test_origin_refuses_invalid_ports(raw: str) -> None:
    refuses(v.origin, raw)


@pytest.mark.parametrize("decoded", [bytes(48), b"k" * 48, b"ab" * 24, bytes(range(15)) * 4])
def test_secret_key_refuses_trivially_low_entropy_keys(decoded: bytes) -> None:
    assert v.secret_key(base64.b64encode(bytes(range(48))).decode())  # precondition
    assert "too little randomness" in refuses(v.secret_key, base64.b64encode(decoded).decode())
