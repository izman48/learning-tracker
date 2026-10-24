"""Per-purpose subkeys of LT_SECRET_KEY: HKDF-SHA256 (RFC 5869) with one label per purpose."""

import pytest

from learning_tracker.keys import PURPOSES, derive_keys, hkdf_sha256


def test_hkdf_matches_rfc5869_test_case_1() -> None:
    okm = hkdf_sha256(
        bytes.fromhex("0b" * 22),
        salt=bytes.fromhex("000102030405060708090a0b0c"),
        info=bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"),
        length=42,
    )
    assert okm.hex() == (
        "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
    )


def test_hkdf_matches_rfc5869_test_case_3_empty_salt_and_info() -> None:
    okm = hkdf_sha256(bytes.fromhex("0b" * 22), salt=b"", info=b"", length=42)
    assert okm.hex() == (
        "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d9d201395faa4b61a96c8"
    )


def test_purpose_labels_are_the_documented_ones() -> None:
    assert PURPOSES == (
        "lt/session/v1",
        "lt/invite/v1",
        "lt/ip/v1",
        "lt/reset/v1",
        "lt/confirm/v1",
    )


def test_every_purpose_gets_a_distinct_32_byte_key_unlike_the_secret() -> None:
    secret = b"s" * 48
    keys = derive_keys(secret)
    values = [keys.session, keys.invite, keys.ip, keys.reset, keys.confirm]
    assert all(len(v) == 32 for v in values)
    assert len(set(values)) == len(values)
    assert secret[:32] not in values


def test_keys_are_deterministic_and_change_with_the_secret() -> None:
    assert derive_keys(b"a" * 48) == derive_keys(b"a" * 48)
    assert derive_keys(b"a" * 48).session != derive_keys(b"b" * 48).session


def test_short_secret_is_refused() -> None:
    assert derive_keys(b"x" * 32)  # precondition: 32 bytes is enough
    with pytest.raises(ValueError, match="at least 32 bytes"):
        derive_keys(b"x" * 31)


def test_repr_names_the_keys_but_never_shows_key_material() -> None:
    keys = derive_keys(b"s" * 48)
    assert "session=<32 bytes>" in repr(keys)
    for value in (keys.session, keys.invite, keys.ip, keys.reset, keys.confirm):
        assert value.hex() not in repr(keys)
        assert repr(value) not in repr(keys)
