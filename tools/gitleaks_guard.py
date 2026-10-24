"""Check that a gitleaks run really scanned the whole history.

gitleaks can print "no leaks found" and exit 0 after scanning nothing, for example
when git inside its container refuses the repo ("dubious ownership"). This guard
reads the log and fails unless it scanned exactly as many commits as the history
holds and logged no errors.

    python -m tools.gitleaks_guard <gitleaks.log> <expected commit count>
"""

import re
import sys
from pathlib import Path

SCANNED = re.compile(r"\bINF (\d+) commits scanned")
ERROR_LINE = re.compile(r"\bERR\b")


def problems(log: str, expected_commits: int) -> list[str]:
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
    log_path, expected = argv
    found = problems(Path(log_path).read_text(), int(expected))
    for problem in found:
        print(problem)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
