"""Invite codes: 100 random bits, shown once, stored only as an HMAC."""

import re

import pytest

from learning_tracker.web.auth.invites import (
    InvalidCode,
    code_hash,
    new_code,
    normalise,
)

KEY = b"v" * 32


def test_codes_are_four_groups_of_five_crockford_characters() -> None:
    codes = {new_code() for _ in range(200)}

    assert len(codes) == 200
    assert all(re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{5}(-[0-9A-HJKMNP-TV-Z]{5}){3}", c) for c in codes)


def test_codes_carry_100_bits() -> None:
    # 20 symbols from a 32-symbol alphabet: 20 * 5 = 100 bits.
    assert len(normalise(new_code())) == 20


@pytest.mark.parametrize(
    "typed",
    [
        "abcde-fghjk-mnpqr-stvwx",
        "ABCDE FGHJK MNPQR STVWX",
        "ABCDEFGHJKMNPQRSTVWX",
        " abcde-fghjk-mnpqr-stvwx ",
    ],
)
def test_typing_variations_normalise_to_the_same_code(typed: str) -> None:
    assert normalise(typed) == "ABCDEFGHJKMNPQRSTVWX"


def test_easily_confused_letters_are_read_as_digits() -> None:
    assert normalise("OOOOO-IIIII-LLLLL-ooooo") == "00000111111111100000"


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "ABCDE",
        "ABCDE-FGHJK-MNPQR-STVWX-YZ000",
        "ABCDE-FGHJK-MNPQR-STVWU",
        "ABCDE-FGHJK-MNPQR-STVW!",
    ],
)
def test_malformed_codes_are_refused(typed: str) -> None:
    with pytest.raises(InvalidCode):
        normalise(typed)


def test_stored_hash_is_keyed_and_independent_of_typing() -> None:
    code = new_code()

    assert code_hash(KEY, code) == code_hash(KEY, code.lower().replace("-", " "))
    assert code_hash(KEY, code) != code_hash(b"w" * 32, code)
    assert len(code_hash(KEY, code)) == 32
