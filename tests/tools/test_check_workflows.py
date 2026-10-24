"""check_workflows enforces the public-CI rules. Each rule has a self-test that it fires."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.check_workflows import check_repo, check_workflow

PINNED = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
REPO_ROOT = Path(__file__).resolve().parents[2]


def workflow(overrides: dict[Any, Any] | None = None) -> dict[Any, Any]:
    """A minimal workflow that passes every rule; tests break one thing at a time."""
    doc: dict[Any, Any] = {
        True: {"pull_request": None},  # PyYAML reads the key `on` as True
        "permissions": {"contents": "read"},
        "jobs": {
            "api": {
                "runs-on": "ubuntu-24.04",
                "steps": [
                    {"uses": PINNED, "with": {"persist-credentials": False}},
                    {"run": "uv run pytest"},
                ],
            },
            "required": {"if": "always()", "needs": ["api"], "runs-on": "ubuntu-24.04"},
        },
    }
    doc.update(overrides or {})
    return doc


def steps(doc: dict[Any, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = doc["jobs"]["api"]["steps"]
    return result


def test_the_baseline_workflow_passes() -> None:
    assert check_workflow("ci.yml", workflow()) == []


def test_the_repo_workflows_pass() -> None:
    files = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert files  # precondition: there are workflows to check

    assert check_repo(REPO_ROOT / ".github" / "workflows") == []


@pytest.mark.parametrize(
    "ref",
    [
        "actions/checkout@v7",
        "actions/checkout@main",
        "actions/checkout@3d3c42e",
        "docker://alpine:3.20",
    ],
)
def test_unpinned_action_fails(ref: str) -> None:
    doc = workflow()
    steps(doc)[0] = {"uses": ref, "with": {"persist-credentials": False}}

    assert any("pinned" in p for p in check_workflow("ci.yml", doc))


def test_docker_action_pinned_by_digest_passes() -> None:
    doc = workflow()
    steps(doc).append({"uses": "docker://alpine:3.20@sha256:" + "a" * 64})

    assert check_workflow("ci.yml", doc) == []


@pytest.mark.parametrize("trigger", ["pull_request_target", "workflow_run"])
def test_dangerous_trigger_fails(trigger: str) -> None:
    doc = workflow({True: {trigger: None}})

    assert any(trigger in p for p in check_workflow("ci.yml", doc))


def test_missing_top_level_permissions_fails() -> None:
    doc = workflow()
    del doc["permissions"]

    assert any("permissions" in p for p in check_workflow("ci.yml", doc))


def test_write_all_permissions_fails() -> None:
    assert any(
        "permissions" in p for p in check_workflow("ci.yml", workflow({"permissions": "write-all"}))
    )


def test_checkout_without_persist_credentials_false_fails() -> None:
    doc = workflow()
    steps(doc)[0] = {"uses": PINNED}

    assert any("persist-credentials" in p for p in check_workflow("ci.yml", doc))


@pytest.mark.parametrize(
    "step",
    [
        {"uses": "actions/cache@" + "b" * 40},
        {"uses": "astral-sh/setup-uv@" + "c" * 40, "with": {"enable-cache": True}},
        {"uses": "actions/setup-node@" + "d" * 40, "with": {"cache": "npm"}},
    ],
)
def test_cache_writes_reachable_from_pull_requests_fail(step: dict[str, Any]) -> None:
    doc = workflow()
    steps(doc).append(step)

    assert any("cache" in p for p in check_workflow("ci.yml", doc))


def test_cache_is_allowed_when_no_pull_request_trigger() -> None:
    doc = workflow({True: {"schedule": [{"cron": "0 3 * * 1"}]}})
    steps(doc).append({"uses": "actions/cache@" + "b" * 40})

    assert check_workflow("audit.yml", doc) == []


@pytest.mark.parametrize("command", ["npm ci", "npm install", "cd ui && npm ci --no-audit"])
def test_npm_install_without_ignore_scripts_fails(command: str) -> None:
    doc = workflow()
    steps(doc).append({"run": command})

    assert any("--ignore-scripts" in p for p in check_workflow("ci.yml", doc))


def test_npm_ci_with_ignore_scripts_passes() -> None:
    doc = workflow()
    steps(doc).append({"run": "npm ci --ignore-scripts"})

    assert check_workflow("ci.yml", doc) == []


def test_docker_run_without_digest_fails() -> None:
    doc = workflow()
    steps(doc).append({"run": "docker run --rm example/tool:1.0 scan"})

    assert any("digest" in p for p in check_workflow("ci.yml", doc))


def test_job_missing_from_required_fails() -> None:
    doc = workflow()
    doc["jobs"]["lint"] = {"runs-on": "ubuntu-24.04", "steps": [{"run": "true"}]}

    assert any("required" in p and "lint" in p for p in check_workflow("ci.yml", doc))


def test_required_job_must_run_always() -> None:
    doc = workflow()
    del doc["jobs"]["required"]["if"]

    assert any("always()" in p for p in check_workflow("ci.yml", doc))


def test_check_repo_parses_the_on_key(tmp_path: Path) -> None:
    (tmp_path / "bad.yml").write_text(
        yaml.safe_dump({"on": {"pull_request_target": None}, "permissions": {}, "jobs": {}})
    )

    assert any("pull_request_target" in p for p in check_repo(tmp_path))


@pytest.mark.parametrize("trigger", ["issue_comment", "pull_request_review", "repository_dispatch"])
def test_triggers_outside_the_allow_list_fail(trigger: str) -> None:
    doc = workflow({True: {trigger: None}})

    assert any(trigger in p for p in check_workflow("ci.yml", doc))


@pytest.mark.parametrize("permissions", [{"contents": "write"}, {"packages": "write"}])
def test_top_level_write_permissions_fail(permissions: dict[str, str]) -> None:
    doc = workflow({"permissions": permissions})

    assert any("permissions" in p for p in check_workflow("ci.yml", doc))


def test_job_write_permission_outside_the_allow_list_fails() -> None:
    doc = workflow()
    doc["jobs"]["api"]["permissions"] = {"contents": "write"}

    assert any("contents: write" in p for p in check_workflow("ci.yml", doc))


def test_allow_listed_job_write_permission_passes() -> None:
    doc = workflow(
        {
            "jobs": {
                "analyze": {
                    "runs-on": "ubuntu-24.04",
                    "permissions": {"contents": "read", "security-events": "write"},
                    "steps": [{"run": "true"}],
                }
            }
        }
    )

    assert check_workflow("codeql.yml", doc) == []


def test_ci_without_a_required_job_fails() -> None:
    doc = workflow()
    del doc["jobs"]["required"]

    assert any("required" in p for p in check_workflow("ci.yml", doc))


def test_docker_run_must_use_a_digest_pinned_image_variable() -> None:
    doc = workflow()
    # A digest elsewhere in the command does not pin the image that runs.
    steps(doc).append({"run": "docker run --rm example/tool:1.0 --label x@sha256:" + "a" * 64})

    assert any("digest" in p for p in check_workflow("ci.yml", doc))


def test_docker_run_with_a_pinned_image_env_passes() -> None:
    doc = workflow()
    steps(doc).append(
        {
            "env": {"IMAGE": "example/tool:1.0@sha256:" + "a" * 64},
            "run": 'docker run --rm "$IMAGE" scan',
        }
    )

    assert check_workflow("ci.yml", doc) == []


@pytest.mark.parametrize("image", ["postgres:18.6-alpine", "postgres@sha256:abc"])
def test_service_and_container_images_must_be_pinned_by_digest(image: str) -> None:
    doc = workflow()
    doc["jobs"]["api"]["services"] = {"postgres": {"image": image}}

    assert any("digest" in p for p in check_workflow("ci.yml", doc))
