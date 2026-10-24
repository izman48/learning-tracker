"""The repo is public: any email address in tracked text uses a reserved example domain."""

import re

from tests.repo.conftest import tracked_files

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
ALLOWED_DOMAIN = re.compile(r"@(?:[A-Za-z0-9-]+\.)*example\.(?:com|org|net)$", re.IGNORECASE)
SKIP_SUFFIXES = {".lock"}
SKIP_NAMES = {"LICENSE"}


def offending_emails(text: str) -> list[str]:
    return [m.group(0) for m in EMAIL.finditer(text) if not ALLOWED_DOMAIN.search(m.group(0))]


def test_tracked_text_uses_example_domains_only() -> None:
    files = [
        f for f in tracked_files() if f.suffix not in SKIP_SUFFIXES and f.name not in SKIP_NAMES
    ]
    assert files  # precondition: we are scanning something

    found = {
        str(f): hits
        for f in files
        if f.is_file() and (hits := offending_emails(f.read_text(errors="ignore")))
    }

    assert found == {}


def test_detector_fires_on_a_non_example_address() -> None:
    address = "admin" + "@" + "mysite.io"  # built at runtime so this file passes its own scan
    assert offending_emails(f"contact {address}") == [address]
    assert offending_emails("contact admin@reads.example.com") == []
    assert offending_emails("contact Admin@Reads.EXAMPLE.com") == []  # domains ignore case
