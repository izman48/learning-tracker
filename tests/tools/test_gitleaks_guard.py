"""The gitleaks job must prove it scanned the whole history, not just exit 0."""

from pathlib import Path

import yaml

from tools.gitleaks_guard import problems

REPO_ROOT = Path(__file__).resolve().parents[2]
GOOD_LOG = "9:29AM INF 4 commits scanned.\n9:29AM INF no leaks found\n"


def test_a_full_clean_scan_passes() -> None:
    assert problems(GOOD_LOG, expected_commits=4) == []


def test_zero_commits_scanned_fails() -> None:
    log = "9:28AM ERR [git] fatal: detected dubious ownership\n9:28AM INF 0 commits scanned.\n"
    assert problems(log, expected_commits=4) != []


def test_fewer_commits_than_the_history_fails() -> None:
    assert any("3 of 4" in p for p in problems(GOOD_LOG.replace("4 commits", "3 commits"), 4))


def test_an_error_line_fails_even_with_the_right_count() -> None:
    assert any("ERR" in p for p in problems(GOOD_LOG + "9:29AM ERR something\n", 4))


def test_a_log_without_the_summary_fails() -> None:
    assert problems("", expected_commits=4) != []


def test_ci_secrets_job_runs_the_guard() -> None:
    ci = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    commands = "\n".join(step.get("run", "") for step in ci["jobs"]["secrets"]["steps"])

    assert "gitleaks" in commands
    assert "tools.gitleaks_guard" in commands
    assert "git rev-list --all --count" in commands
