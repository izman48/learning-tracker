"""Which IP addresses the fetcher may connect to.

Fail closed: an address is allowed only if it parses, has no IPv6 zone id, is in
none of the refused ranges below, and Python's `ipaddress` also calls it global.
The explicit table repeats what `ipaddress` already treats as non-global, so a
change in a future Python release cannot quietly open a range.
"""

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

_REFUSED_V4 = tuple(
    IPv4Network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local, including cloud metadata 169.254.169.254
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",  # 6to4 relay anycast
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, including broadcast
    )
)

# IPv6 must be global unicast (2000::/3); everything outside it is refused. Inside it,
# SIIT, NAT64, 6to4 and Teredo can carry an internal IPv4 address in a public-looking
# IPv6 one. No article needs them, so they are refused outright, not unwrapped.
_GLOBAL_UNICAST_V6 = IPv6Network("2000::/3")
_REFUSED_V6 = tuple(
    IPv6Network(net)
    for net in (
        "::/96",  # unspecified and the deprecated IPv4-compatible form
        "::1/128",
        "::ffff:0:0:0/96",  # IPv4-translated (SIIT); not the mapped ::ffff:0:0/96
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # NAT64, local use
        "2002::/16",  # 6to4
        # IETF protocol assignments (Teredo, ORCHID, AMT, ...): no web content lives here.
        "2001::/23",
        "2001:db8::/32",  # documentation
        "100::/64",  # discard-only
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
        "fec0::/10",  # site-local (deprecated)
        "ff00::/8",  # multicast
    )
)

_MAPPED_V4 = IPv6Network("::ffff:0:0/96")


def is_public_address(text: str) -> bool:
    try:
        addr = ip_address(text)
    except ValueError:
        return False
    if isinstance(addr, IPv6Address):
        if addr.scope_id is not None:
            return False  # a zone id pins an interface; never from a public DNS answer
        if addr in _MAPPED_V4 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped  # ::ffff:a.b.c.d is judged as a.b.c.d
    if isinstance(addr, IPv4Address):
        refused = any(addr in net for net in _REFUSED_V4)
    else:
        refused = addr not in _GLOBAL_UNICAST_V6 or any(addr in net for net in _REFUSED_V6)
    return not refused and addr.is_global
