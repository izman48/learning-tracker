"""argon2id parameters are pinned, so a library default change can't weaken them."""

from learning_tracker.web.auth.passwords import password_helper


def test_hashes_use_argon2id_with_the_pinned_cost() -> None:
    hashed = password_helper.hash("a passphrase for tests")

    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_verification_round_trips() -> None:
    hashed = password_helper.hash("a passphrase for tests")

    assert password_helper.verify_and_update("a passphrase for tests", hashed)[0] is True
    assert password_helper.verify_and_update("another passphrase", hashed)[0] is False
