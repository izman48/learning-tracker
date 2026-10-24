import subprocess
from pathlib import Path

import pytest


def is_ignored(repo_root: Path, path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", path], cwd=repo_root, check=False
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    "path", [".env", ".env.local", ".env.production", "backups/lt-2026.dump", "old.dump"]
)
def test_secrets_and_dumps_are_ignored(repo_root: Path, path: str) -> None:
    assert is_ignored(repo_root, path)


def test_env_example_is_not_ignored(repo_root: Path) -> None:
    assert not is_ignored(repo_root, ".env.example")
