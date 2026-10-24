"""Per-purpose subkeys of LT_SECRET_KEY (ADR-LT-001 D-3, sec T-21).

Code never uses LT_SECRET_KEY directly. Each purpose gets its own HKDF-SHA256
subkey, so a key leaked or misused in one place cannot forge values for another.
HKDF (RFC 5869) is a few lines over the standard library's hmac and is checked
against the RFC's test vectors, which is simpler than adding a crypto dependency.

Rotating LT_SECRET_KEY changes every subkey: everyone is signed out, unredeemed
invite codes and outstanding reset/confirm links stop working, and rate-limit
buckets start fresh.
"""

import hashlib
import hmac
from dataclasses import dataclass, field

MIN_SECRET_BYTES = 32
PURPOSES = ("lt/session/v1", "lt/invite/v1", "lt/ip/v1", "lt/reset/v1", "lt/confirm/v1")
_HASH_LEN = hashlib.sha256().digest_size


def hkdf_sha256(ikm: bytes, *, salt: bytes, info: bytes, length: int = 32) -> bytes:
    if length > 255 * _HASH_LEN:
        raise ValueError("HKDF output too long")
    prk = hmac.new(salt or bytes(_HASH_LEN), ikm, hashlib.sha256).digest()
    okm, block = b"", b""
    for counter in range(1, -(-length // _HASH_LEN) + 1):
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
    return okm[:length]


@dataclass(frozen=True)
class Keys:
    session: bytes = field(repr=False)
    invite: bytes = field(repr=False)
    ip: bytes = field(repr=False)
    reset: bytes = field(repr=False)
    confirm: bytes = field(repr=False)

    def __repr__(self) -> str:
        names = ", ".join(
            f"{name}=<{len(getattr(self, name))} bytes>" for name in self.__dataclass_fields__
        )
        return f"Keys({names})"


def derive_keys(secret: bytes) -> Keys:
    if len(secret) < MIN_SECRET_BYTES:
        raise ValueError(f"LT_SECRET_KEY must decode to at least {MIN_SECRET_BYTES} bytes")
    session, invite, ip, reset, confirm = (
        hkdf_sha256(secret, salt=b"", info=label.encode()) for label in PURPOSES
    )
    return Keys(session=session, invite=invite, ip=ip, reset=reset, confirm=confirm)
