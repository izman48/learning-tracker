"""Startup checks against the Models API: every configured model exists, is not
retired, and supports what its step needs; the search model supports web search.
A mock HTTP transport stands in for the API."""

import json
from typing import Any

import httpx2
import pytest

from learning_tracker.worker.models.client import make_sdk_client
from learning_tracker.worker.models.startup import SEARCH_SETTING, check_models

pytestmark = pytest.mark.anyio

MODELS = {
    "LT_MODEL_SEARCH": "claude-haiku-5-5",
    "LT_MODEL_PROFILE": "claude-haiku-5-5",
    "LT_MODEL_SCREEN": "claude-haiku-5-5",
    "LT_MODEL_CHOOSE": "claude-sonnet-5-5",
    "LT_MODEL_EXPLAIN": "claude-haiku-5-5",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def model(
    model_id: str,
    *,
    structured: bool = True,
    web_search: bool = True,
    lifecycle: str = "active",
    capabilities: bool = True,
    thinking_off: bool = True,
    effort_low: bool = True,
) -> dict[str, Any]:
    caps = {
        "thinking": {"supported": True, "types": {"disabled": {"supported": thinking_off}}},
        "effort": {"supported": True, "low": {"supported": effort_low}},
        "structured_outputs": {"supported": structured},
        "server_tools": {"supported": web_search, "web_search": {"supported": web_search},
                         "code_execution": {"supported": False}},
    }  # fmt: skip
    return {
        "type": "model",
        "id": model_id,
        "display_name": model_id,
        "created_at": "2026-09-01T00:00:00Z",
        "lifecycle": lifecycle,
        "capabilities": caps if capabilities else None,
    }


def catalogue(models: dict[str, dict[str, Any] | int]) -> tuple[Any, list[str]]:
    asked: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        model_id = request.url.path.rsplit("/", 1)[-1]
        asked.append(model_id)
        answer = models.get(model_id, 404)
        if isinstance(answer, int):
            error = {"type": "error", "error": {"type": "not_found_error", "message": "x"}}
            return httpx2.Response(answer, json=error)
        return httpx2.Response(200, content=json.dumps(answer))

    return make_sdk_client("test-key-not-real", transport=httpx2.MockTransport(handle)), asked


async def test_a_healthy_configuration_has_no_problems_and_asks_once_per_model() -> None:
    client, asked = catalogue({m: model(m) for m in set(MODELS.values())})
    result = await check_models(MODELS, client)
    assert (result.problems, result.warnings) == ([], [])
    assert sorted(asked) == ["claude-haiku-5-5", "claude-sonnet-5-5"]


async def test_an_unknown_model_is_a_problem_naming_its_setting() -> None:
    client, _ = catalogue({"claude-haiku-5-5": model("claude-haiku-5-5")})
    result = await check_models(MODELS, client)
    assert result.problems == ["LT_MODEL_CHOOSE=claude-sonnet-5-5: model not found"]


async def test_a_retired_model_is_a_problem_and_a_deprecated_one_a_warning() -> None:
    client, _ = catalogue({
        "claude-haiku-5-5": model("claude-haiku-5-5", lifecycle="retired"),
        "claude-sonnet-5-5": model("claude-sonnet-5-5", lifecycle="deprecated"),
    })  # fmt: skip
    result = await check_models(MODELS, client)
    assert "LT_MODEL_SCREEN=claude-haiku-5-5: model is retired" in result.problems
    assert result.warnings == ["LT_MODEL_CHOOSE=claude-sonnet-5-5: model is deprecated"]


async def test_steps_need_structured_outputs() -> None:
    client, _ = catalogue({
        "claude-haiku-5-5": model("claude-haiku-5-5"),
        "claude-sonnet-5-5": model("claude-sonnet-5-5", structured=False),
    })  # fmt: skip
    result = await check_models(MODELS, client)
    assert result.problems == [
        "LT_MODEL_CHOOSE=claude-sonnet-5-5: model does not support structured outputs"
    ]


async def test_the_search_model_needs_web_search() -> None:
    models = {**MODELS, SEARCH_SETTING: "claude-sonnet-5-5"}
    client, _ = catalogue({
        "claude-haiku-5-5": model("claude-haiku-5-5"),
        "claude-sonnet-5-5": model("claude-sonnet-5-5", web_search=False),
    })  # fmt: skip
    result = await check_models(models, client)
    assert result.problems == [
        "LT_MODEL_SEARCH=claude-sonnet-5-5: model does not support the web search tool"
    ]


async def test_missing_capabilities_count_as_unsupported() -> None:
    client, _ = catalogue({m: model(m, capabilities=False) for m in set(MODELS.values())})
    result = await check_models(MODELS, client)
    assert len(result.problems) == len(MODELS) + 1  # every step, plus web search


@pytest.mark.parametrize("status", [401, 403])
async def test_a_rejected_key_is_one_clear_problem(status: int) -> None:
    client, _ = catalogue({"claude-haiku-5-5": status, "claude-sonnet-5-5": status})
    result = await check_models(MODELS, client)
    assert result.problems == ["LT_ANTHROPIC_API_KEY was rejected by the provider"]


async def test_an_outage_is_reported_without_guessing_about_models() -> None:
    client, _ = catalogue({"claude-haiku-5-5": 503, "claude-sonnet-5-5": 503})
    result = await check_models(MODELS, client)
    assert result.problems == ["could not reach the provider to check models (503); try again"]


async def test_the_check_reports_which_models_take_thinking_off_and_low_effort() -> None:
    client, _ = catalogue({
        "claude-haiku-5-5": model("claude-haiku-5-5"),
        "claude-sonnet-5-5": model("claude-sonnet-5-5", thinking_off=False, effort_low=True),
    })  # fmt: skip
    result = await check_models(MODELS, client)
    assert result.tuning.thinking_off == {"claude-haiku-5-5"}
    assert result.tuning.effort_low == {"claude-haiku-5-5", "claude-sonnet-5-5"}
