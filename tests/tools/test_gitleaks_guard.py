"""The gitleaks job must prove it scanned the whole history, not just exit 0."""

import subprocess
from pathlib import Path

import pytest
import yaml

from tools.gitleaks_guard import expected_commits, problems

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


def test_an_expected_count_below_one_fails() -> None:
    # An empty or failed rev-list must not let "0 of 0" pass.
    assert problems("9:29AM INF 0 commits scanned.\n", expected_commits=0) != []


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_expected_count_excludes_merge_commits(tmp_path: Path) -> None:
    # gitleaks scans `git log -p --all`, which yields no patch for a merge commit,
    # so a PR run's merge commit must not be expected. (Content that exists only
    # in a merge's conflict resolution is not scanned: a known gitleaks limit.)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "base")
    git(tmp_path, "switch", "-q", "-c", "topic")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "topic")
    git(tmp_path, "switch", "-q", "main")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "main")
    git(tmp_path, "merge", "-q", "--no-ff", "-m", "merge", "topic")

    assert expected_commits(tmp_path, "all") == 3


def test_head_scope_counts_only_the_checked_out_ancestry(tmp_path: Path) -> None:
    # On a pull request, another branch's commits must not be expected (or scanned).
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "base")
    git(tmp_path, "switch", "-q", "-c", "elsewhere")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "another branch")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "and another")
    git(tmp_path, "switch", "-q", "main")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "the pr")

    assert expected_commits(tmp_path, "head") == 2
    assert expected_commits(tmp_path, "all") == 4


def test_scope_must_be_known() -> None:
    with pytest.raises(ValueError, match="scope"):
        expected_commits(REPO_ROOT, "everything")


def workflow_runs(name: str, job: str) -> str:
    doc = yaml.safe_load((REPO_ROOT / ".github/workflows" / name).read_text())
    return "\n".join(step.get("run", "") for step in doc["jobs"][job]["steps"])


def test_pull_requests_scan_their_own_ancestry() -> None:
    commands = workflow_runs("ci.yml", "secrets")

    assert "gitleaks" in commands and "--log-opts=HEAD" in commands
    assert "--log-opts=--all" not in commands
    assert "tools.gitleaks_guard" in commands and "--scope head" in commands


def test_main_and_the_weekly_run_sweep_every_branch() -> None:
    doc = yaml.safe_load((REPO_ROOT / ".github/workflows/audit.yml").read_text())
    triggers = doc.get("on", doc.get(True))
    assert "schedule" in triggers and "push" in triggers  # precondition: when it runs
    commands = workflow_runs("audit.yml", "secrets")

    assert "--log-opts=--all" in commands
    assert "tools.gitleaks_guard" in commands and "--scope all" in commands
