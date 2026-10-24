"""Check that a gitleaks run really scanned the whole history.

gitleaks can print "no leaks found" and exit 0 after scanning nothing, for example
when git inside its container refuses the repo ("dubious ownership"). This guard
reads the log and fails unless it scanned exactly as many non-merge commits as the
history holds and logged no errors.

Merge commits are excluded from the expected count: gitleaks scans `git log -p --all`,
which yields no patch for a merge, and a pull_request run checks out GitHub's merge
commit. Content that exists only in a merge's conflict resolution is therefore not
scanned; that is a known gitleaks limit.

    python -m tools.gitleaks_guard <gitleaks.log>    (run from the repo root)
"""

import re
import subprocess
import sys
from pathlib import Path

SCANNED = re.compile(r"\bINF (\d+) commits scanned")
ERROR_LINE = re.compile(r"\bERR\b")


def expected_commits(repo: Path) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--all", "--no-merges", "--count"],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return int(out.strip())


def problems(log: str, expected_commits: int) -> list[str]:
    if expected_commits < 1:
        return [f"expected commit count is {expected_commits}: the history was not read"]
    found = []
    counts = [int(m.group(1)) for m in SCANNED.finditer(log)]
    if not counts:
        found.append("no 'commits scanned' line: gitleaks did not report a scan")
    elif counts[-1] != expected_commits:
        found.append(f"scanned {counts[-1]} of {expected_commits} commits")
    if any(ERROR_LINE.search(line) for line in log.splitlines()):
        found.append("gitleaks logged an ERR line")
    return found


def main(argv: list[str]) -> int:
    (log_path,) = argv
    found = problems(Path(log_path).read_text(), expected_commits(Path.cwd()))
    for problem in found:
        print(problem)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
