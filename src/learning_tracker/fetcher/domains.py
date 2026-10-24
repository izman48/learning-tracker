"""Registrable domains (eTLD+1) from the Public Suffix List bundled with the package.

The list ships inside `publicsuffixlist`; nothing is downloaded at runtime. Private
suffixes such as github.io count, so alice.github.io and bob.github.io are different
sites, as they have different owners.
"""

from functools import cache

from publicsuffixlist import PublicSuffixList


@cache
def _psl() -> PublicSuffixList:
    return PublicSuffixList()


def registrable_domain(host: str) -> str:
    """The site a host belongs to; a bare public suffix stands for itself."""
    name = host.lower().rstrip(".")
    return _psl().privatesuffix(name) or name


def same_site(host_a: str, host_b: str) -> bool:
    return registrable_domain(host_a) == registrable_domain(host_b)
