"""Check GitHub Actions workflows against the public-repo CI rules.

This repo is public, so anyone can open a pull request that runs our workflows.
Each rule below closes one way a fork PR or a moved tag could reach secrets,
poison a cache, or run code we did not review. Run from the repo root:

    uv run python -m tools.check_workflows
"""

import re
import sys
from pathlib import Path
from typing import Any

import yaml

SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")
DOCKER_PIN = re.compile(r"^docker://\S+@sha256:[0-9a-f]{64}$")
NPM_INSTALL = re.compile(r"\bnpm\s+(ci|install|i)\b")
DOCKER_RUN = re.compile(r"\bdocker\s+run\b")
FORBIDDEN_TRIGGERS = ("pull_request_target", "workflow_run")
CACHE_ACTIONS = ("actions/cache@", "actions/cache/save@")


def triggers(doc: dict[Any, Any]) -> set[str]:
    # PyYAML (YAML 1.1) reads the bare key `on` as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    if isinstance(on, dict):
        return set(on)
    return set()


def all_steps(doc: dict[Any, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (job_name, step)
        for job_name, job in (doc.get("jobs") or {}).items()
        for step in (job.get("steps") or [])
    ]


def check_pins(doc: dict[Any, Any]) -> list[str]:
    problems = []
    refs = [(name, step.get("uses")) for name, step in all_steps(doc)]
    refs += [(name, job.get("uses")) for name, job in (doc.get("jobs") or {}).items()]
    for job_name, ref in refs:
        if not ref or ref.startswith("./"):
            continue
        pinned = DOCKER_PIN.match(ref) if ref.startswith("docker://") else SHA_PIN.match(ref)
        if not pinned:
            problems.append(f"job {job_name}: {ref} is not pinned to a full commit SHA or digest")
    return problems


def check_triggers(doc: dict[Any, Any]) -> list[str]:
    return [f"trigger {t} is not allowed" for t in FORBIDDEN_TRIGGERS if t in triggers(doc)]


def check_permissions(doc: dict[Any, Any]) -> list[str]:
    permissions = doc.get("permissions")
    if permissions is None:
        return ["top-level permissions block is missing"]
    if permissions == "write-all":
        return ["top-level permissions must not be write-all"]
    return []


def check_checkout(doc: dict[Any, Any]) -> list[str]:
    return [
        f"job {job_name}: actions/checkout must set persist-credentials: false"
        for job_name, step in all_steps(doc)
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and (step.get("with") or {}).get("persist-credentials") is not False
    ]


def writes_cache(step: dict[str, Any]) -> bool:
    uses = str(step.get("uses", ""))
    options = step.get("with") or {}
    if uses.startswith(CACHE_ACTIONS):
        return True
    if uses.startswith("astral-sh/setup-uv@"):
        return options.get("enable-cache") not in (None, False, "false")
    if uses.startswith(("actions/setup-node@", "actions/setup-python@")):
        return bool(options.get("cache"))
    return False


def check_cache(doc: dict[Any, Any]) -> list[str]:
    # A fork PR that can write a cache entry can poison what main later restores.
    if "pull_request" not in triggers(doc):
        return []
    return [
        f"job {job_name}: cache writes are not allowed in a workflow fork PRs can trigger"
        for job_name, step in all_steps(doc)
        if writes_cache(step)
    ]


def check_run_commands(doc: dict[Any, Any]) -> list[str]:
    problems = []
    for job_name, step in all_steps(doc):
        command = str(step.get("run", ""))
        if NPM_INSTALL.search(command) and "--ignore-scripts" not in command:
            problems.append(f"job {job_name}: npm install/ci must use --ignore-scripts")
        if DOCKER_RUN.search(command) and "@sha256:" not in command:
            problems.append(f"job {job_name}: docker run images must be pinned by digest")
    return problems


def check_required(doc: dict[Any, Any]) -> list[str]:
    jobs = doc.get("jobs") or {}
    required = jobs.get("required")
    if required is None:
        return []
    problems = []
    if "always()" not in str(required.get("if", "")):
        problems.append("job required must run with if: always()")
    needs = set(required.get("needs") or [])
    for name in sorted(set(jobs) - {"required"} - needs):
        problems.append(f"job {name} is missing from required.needs")
    return problems


RULES = (
    check_pins,
    check_triggers,
    check_permissions,
    check_checkout,
    check_cache,
    check_run_commands,
    check_required,
)


def check_workflow(name: str, doc: dict[Any, Any]) -> list[str]:
    return [f"{name}: {problem}" for rule in RULES for problem in rule(doc)]


def check_repo(workflows_dir: Path) -> list[str]:
    problems = []
    for path in sorted([*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")]):
        problems += check_workflow(path.name, yaml.safe_load(path.read_text()) or {})
    return problems


def main() -> int:
    problems = check_repo(Path(".github/workflows"))
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
