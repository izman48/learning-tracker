"""Registrable domains (eTLD+1), used to refuse redirects to a different site."""

import pytest

from learning_tracker.fetcher.domains import registrable_domain, same_site


@pytest.mark.parametrize(
    "host,expected",
    [
        ("www.example.com", "example.com"),
        ("example.com", "example.com"),
        ("blog.example.co.uk", "example.co.uk"),
        ("a.b.site.example", "site.example"),
        ("user.github.io", "user.github.io"),  # private suffix: each user is its own site
        ("EXAMPLE.COM.", "example.com"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected


def test_same_site_allows_www_and_subdomains_but_not_other_owners() -> None:
    assert same_site("example.com", "www.example.com")
    assert same_site("news.example.co.uk", "example.co.uk")
    assert not same_site("example.com", "example.org")
    assert not same_site("alice.github.io", "bob.github.io")
    assert not same_site("example.co.uk", "other.co.uk")


def test_a_bare_public_suffix_is_only_the_same_site_as_itself() -> None:
    assert registrable_domain("co.uk") == "co.uk"
    assert not same_site("co.uk", "example.co.uk")
