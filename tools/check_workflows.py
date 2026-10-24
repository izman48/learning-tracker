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
IMAGE_PIN = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
# Allow-lists, not deny-lists: anything new needs a deliberate edit here.
ALLOWED_TRIGGERS = {"pull_request", "push", "schedule", "workflow_dispatch"}
READ_ONLY = {"read", "none"}
# (workflow file, job, permission) that may be granted write.
ALLOWED_JOB_WRITES = {("codeql.yml", "analyze", "security-events")}
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


def check_pins(name: str, doc: dict[Any, Any]) -> list[str]:
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


def check_triggers(name: str, doc: dict[Any, Any]) -> list[str]:
    return [f"trigger {t} is not allowed" for t in sorted(triggers(doc) - ALLOWED_TRIGGERS)]


def write_grants(permissions: Any) -> list[str]:
    if isinstance(permissions, str):
        return [] if permissions in ("read-all", "{}") else [permissions]
    if isinstance(permissions, dict):
        return [scope for scope, level in permissions.items() if level not in READ_ONLY]
    return []


def check_permissions(name: str, doc: dict[Any, Any]) -> list[str]:
    permissions = doc.get("permissions")
    if permissions is None:
        return ["top-level permissions block is missing"]
    problems = [
        f"top-level permissions must be read-only, not {g}" for g in write_grants(permissions)
    ]
    for job_name, job in (doc.get("jobs") or {}).items():
        for scope in write_grants(job.get("permissions")):
            if (name, job_name, scope) not in ALLOWED_JOB_WRITES:
                problems.append(f"job {job_name}: permissions {scope}: write is not allow-listed")
    return problems


def check_checkout(name: str, doc: dict[Any, Any]) -> list[str]:
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


def check_cache(name: str, doc: dict[Any, Any]) -> list[str]:
    # A fork PR that can write a cache entry can poison what main later restores.
    if "pull_request" not in triggers(doc):
        return []
    return [
        f"job {job_name}: cache writes are not allowed in a workflow fork PRs can trigger"
        for job_name, step in all_steps(doc)
        if writes_cache(step)
    ]


def check_run_commands(name: str, doc: dict[Any, Any]) -> list[str]:
    problems = []
    for job_name, step in all_steps(doc):
        command = str(step.get("run", ""))
        if NPM_INSTALL.search(command) and "--ignore-scripts" not in command:
            problems.append(f"job {job_name}: npm install/ci must use --ignore-scripts")
        runs = len(DOCKER_RUN.findall(command))
        if runs:
            # Every docker run must take its image from the step's IMAGE env, pinned
            # by digest, so the check is about the image that runs, not any text.
            image = str((step.get("env") or {}).get("IMAGE", ""))
            if command.count('"$IMAGE"') < runs or not IMAGE_PIN.match(image):
                problems.append(
                    f'job {job_name}: docker run must use "$IMAGE" from env, pinned by digest'
                )
    return problems


def check_job_images(name: str, doc: dict[Any, Any]) -> list[str]:
    problems = []
    for job_name, job in (doc.get("jobs") or {}).items():
        images = [service.get("image", "") for service in (job.get("services") or {}).values()]
        container = job.get("container")
        images.append(container.get("image", "") if isinstance(container, dict) else container)
        for image in filter(None, images):
            if not IMAGE_PIN.match(str(image)):
                problems.append(f"job {job_name}: image {image} must be pinned by digest")
    return problems


def check_required(name: str, doc: dict[Any, Any]) -> list[str]:
    jobs = doc.get("jobs") or {}
    required = jobs.get("required")
    if required is None:
        # ci.yml's required job is the one status branch policy relies on.
        return ["ci.yml must keep its required job"] if name == "ci.yml" else []
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
    check_job_images,
    check_required,
)


def check_workflow(name: str, doc: dict[Any, Any]) -> list[str]:
    return [f"{name}: {problem}" for rule in RULES for problem in rule(name, doc)]


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
