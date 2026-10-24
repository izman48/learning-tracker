"""Invite codes: 20 Crockford base32 symbols (100 bits from the OS CSPRNG), shown as
four groups of five. The plain code is shown once to the admin who makes it; the
database stores only HMAC-SHA256(invite subkey, normalised code)."""

import hashlib
import hmac
import re
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford: no I, L, O or U
SYMBOLS = 20
_CONFUSABLE = str.maketrans({"O": "0", "I": "1", "L": "1"})
_VALID = re.compile(f"[{ALPHABET}]{{{SYMBOLS}}}")


class InvalidCode(ValueError):
    """Not shaped like one of our codes."""


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(SYMBOLS))
    return "-".join(raw[i : i + 5] for i in range(0, SYMBOLS, 5))


def normalise(typed: str) -> str:
    """Upper-case, drop spaces and hyphens, read O as 0 and I/L as 1."""
    code = re.sub(r"[\s-]", "", typed).upper().translate(_CONFUSABLE)
    if not _VALID.fullmatch(code):
        raise InvalidCode("not an invite code")
    return code


def code_hash(key: bytes, typed: str) -> bytes:
    return hmac.new(key, normalise(typed).encode(), hashlib.sha256).digest()
