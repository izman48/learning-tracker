"""Which addresses the fetcher may connect to: every refused range, and public controls."""

import pytest

from learning_tracker.fetcher.addresses import is_public_address

REFUSED_V4 = [
    "0.0.0.0",  # noqa: S104 - a refused address, not a bind
    "0.1.2.3",  # 0/8
    "10.0.0.1",
    "10.255.255.255",  # 10/8
    "100.64.0.1",
    "100.127.255.254",  # 100.64/10 CGNAT
    "127.0.0.1",
    "127.1.2.3",  # 127/8
    "169.254.169.254",
    "169.254.0.1",  # 169.254/16 link-local + metadata
    "172.16.0.1",
    "172.31.255.255",  # 172.16/12
    "192.0.0.1",  # 192.0.0/24
    "192.0.2.1",  # TEST-NET-1
    "192.88.99.1",  # 6to4 relay anycast
    "192.168.1.1",  # 192.168/16
    "198.18.0.1",
    "198.19.255.255",  # 198.18/15 benchmarking
    "198.51.100.7",  # TEST-NET-2
    "203.0.113.9",  # TEST-NET-3
    "224.0.0.1",
    "239.255.255.250",  # 224/4 multicast
    "240.0.0.1",  # 240/4 reserved
    "255.255.255.255",  # broadcast
]

REFUSED_V6 = [
    "::",  # ::/128 unspecified
    "::1",  # loopback
    "::ffff:127.0.0.1",  # mapped, judged as IPv4
    "::ffff:169.254.169.254",
    "::ffff:10.0.0.1",
    "::127.0.0.1",  # IPv4-compatible (deprecated ::/96)
    "64:ff9b::a9fe:a9fe",  # NAT64 well-known prefix -> 169.254.169.254
    "64:ff9b::808:808",  # NAT64 is denied outright, even for a public v4
    "64:ff9b:1::1",  # NAT64 local-use 64:ff9b:1::/48
    "2002:a9fe:a9fe::",  # 6to4 embedding 169.254.169.254
    "2002:808:808::1",  # 6to4 denied outright, even for a public v4
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo 2001::/32
    "fc00::1",
    "fd12:3456::1",  # ULA fc00::/7
    "fe80::1",  # link-local
    "fec0::1",  # site-local (deprecated)
    "ff02::1",  # multicast
    "2001:db8::1",  # documentation
    "100::1",  # discard-only 100::/64
]

PUBLIC = [
    "93.184.215.14",
    "1.1.1.1",
    "8.8.8.8",
    "2606:4700:4700::1111",
    "2a00:1450:4009:81f::200e",
    "::ffff:8.8.8.8",  # mapped public v4 is judged as that v4
]


@pytest.mark.parametrize("ip", REFUSED_V4)
def test_refuses_non_public_ipv4(ip: str) -> None:
    assert is_public_address(ip) is False


@pytest.mark.parametrize("ip", REFUSED_V6)
def test_refuses_non_public_and_embedding_ipv6(ip: str) -> None:
    assert is_public_address(ip) is False


@pytest.mark.parametrize("ip", PUBLIC)
def test_allows_public_addresses(ip: str) -> None:
    assert is_public_address(ip) is True


@pytest.mark.parametrize("ip", ["fe80::1%eth0", "2606:4700:4700::1111%eth0"])
def test_scoped_ipv6_is_refused_even_when_public(ip: str) -> None:
    assert is_public_address(ip) is False


@pytest.mark.parametrize("junk", ["", "not-an-ip", "999.1.1.1", "1.2.3"])
def test_unparseable_address_is_refused(junk: str) -> None:
    assert is_public_address(junk) is False


def test_the_explicit_refusal_table_is_the_documented_one() -> None:
    # Several ranges are also non-global to `ipaddress`; pinning the table keeps that
    # second layer from being deleted unnoticed.
    from learning_tracker.fetcher import addresses

    assert {str(n) for n in addresses._REFUSED_V4} == {
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16",
        "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
    }  # fmt: skip
    assert {str(n) for n in addresses._REFUSED_V6} == {
        "::/96", "::1/128", "::ffff:0:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16",
        "2001::/23",
        "2001:db8::/32", "100::/64", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8",
    }  # fmt: skip


@pytest.mark.parametrize(
    "ip",
    [
        "::ffff:0:7f00:1",  # IPv4-translated (SIIT) 127.0.0.1
        "::ffff:0:a9fe:a9fe",  # IPv4-translated (SIIT) 169.254.169.254
        "5f00::1",  # SRv6 SIDs, outside global unicast
        "4000::1",  # unassigned, outside 2000::/3
        "2001:20::1",  # ORCHIDv2, inside the IETF special-purpose block 2001::/23
        "2001:10::1",  # deprecated ORCHID
    ],
)
def test_ipv6_must_be_global_unicast_outside_special_purpose_blocks(ip: str) -> None:
    assert is_public_address("2606:4700:4700::1111")  # control: global unicast
    assert is_public_address(ip) is False
