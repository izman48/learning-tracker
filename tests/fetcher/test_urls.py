"""URL checks before any DNS lookup: scheme, port, host spelling, userinfo and length."""

import pytest

from learning_tracker.fetcher.urls import UnsafeURL, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/post",
        "http://example.com/",
        "https://example.com:443/a?b=1",
        "http://example.com:80/a",
        "https://sub.example.com/a#frag",
        "HTTPS://EXAMPLE.COM/Path",
    ],
)
def test_accepts_plain_public_hostnames(url: str) -> None:
    target = validate_url(url)
    assert target.host == target.host.lower()
    assert target.port in (80, 443)


def test_returns_lowercased_ascii_host_and_default_port() -> None:
    t = validate_url("HTTPS://Example.COM/x")
    assert (t.scheme, t.host, t.port) == ("https", "example.com", 443)


def test_idn_host_is_idna_encoded() -> None:
    assert validate_url("https://bücher.example/").host == "xn--bcher-kva.example"


def test_fragment_is_dropped_from_the_request_target() -> None:
    assert "#" not in str(validate_url("https://example.com/a#secret").url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "gopher://example.com/",
        "javascript:alert(1)",
        "data:text/html,hi",
        "ws://example.com/",
        "//example.com/x",
        "example.com/x",
    ],
)
def test_refuses_other_schemes(url: str) -> None:
    with pytest.raises(UnsafeURL, match="scheme"):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8080/",
        "https://example.com:8443/",
        "http://example.com:22/",
        "https://example.com:80/",
        "http://example.com:443/",
        "http://example.com:0/",
    ],
)
def test_refuses_ports_other_than_80_for_http_and_443_for_https(url: str) -> None:
    with pytest.raises(UnsafeURL, match="port"):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://8.8.8.8/",
        "http://[::1]/",
        "http://[64:ff9b::a9fe:a9fe]/",
        "http://[2002:a9fe:a9fe::]/",
        "http://[fec0::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[2606:4700:4700::1111]/",
        # forms that resolvers turn into IPv4 even though they are not dotted quads
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://127.1/",
        "http://0x7f.1/",
        "http://8.8.8.8./",
    ],
)
def test_refuses_ip_literal_hosts_in_every_spelling(url: str) -> None:
    with pytest.raises(UnsafeURL, match="ip literal"):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@example.com/",
        "http://user@example.com/",
        "https://:pass@example.com/",
    ],
)
def test_refuses_userinfo(url: str) -> None:
    with pytest.raises(UnsafeURL, match="userinfo"):
        validate_url(url)


def test_refuses_host_longer_than_253() -> None:
    host = ".".join(["a" * 60] * 4) + ".example.example"  # 243 + 16 = 259 chars
    assert len(host) > 253
    with pytest.raises(UnsafeURL, match="host"):
        validate_url(f"https://{host}/")


@pytest.mark.parametrize(
    "url",
    [
        "http:///path",
        "https://",
        "http://exa mple.com/",
        "http://example.com\\@evil.example/",
        "http://localhost/",
        "http://localhost./",
        "http://intranet/",
        "http://example.com\r\nX: y/",
    ],
)
def test_refuses_missing_malformed_or_single_label_hosts(url: str) -> None:
    with pytest.raises(UnsafeURL):
        validate_url(url)


def test_refuses_overlong_url() -> None:
    with pytest.raises(UnsafeURL, match="length"):
        validate_url("https://example.com/" + "a" * 2100)


def test_a_redirect_location_is_resolved_against_the_current_url_then_checked() -> None:
    t = validate_url("/next", base="https://example.com/a/b")
    assert str(t.url) == "https://example.com/next"
    with pytest.raises(UnsafeURL, match="port"):
        validate_url("//example.com:8080/x", base="https://example.com/")


def test_host_labels_are_matched_whole_not_up_to_a_trailing_newline() -> None:
    from learning_tracker.fetcher import urls

    assert urls._check_host("example.com") == "example.com"  # control
    with pytest.raises(UnsafeURL):
        urls._check_host("example.com\n")
    with pytest.raises(UnsafeURL):
        urls._check_host("exa\nmple.com")


def test_a_trailing_dot_is_stripped_from_host_and_url() -> None:
    target = validate_url("https://example.com./x")
    assert target.host == "example.com"
    assert str(target.url) == "https://example.com/x"
