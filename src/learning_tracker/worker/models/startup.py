"""Check the configured models against the provider's Models API before the worker
starts: each one exists, is not retired, and supports what its step needs. The
search model must support the web search tool. Missing capability data counts as
unsupported, so the check fails closed.

These calls cost nothing; they are not reserved against the spend caps.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import anthropic

from learning_tracker.worker.models.client import ModelTuning

SEARCH_SETTING = "LT_MODEL_SEARCH"


@dataclass(frozen=True)
class ModelCheck:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tuning: ModelTuning = field(default_factory=ModelTuning)


async def check_models(
    models_by_setting: Mapping[str, str], client: anthropic.AsyncAnthropic
) -> ModelCheck:
    infos: dict[str, Any] = {}
    for model_id in sorted(set(models_by_setting.values())):
        try:
            infos[model_id] = await client.models.retrieve(model_id)
        except anthropic.NotFoundError:
            infos[model_id] = None
        except anthropic.AuthenticationError, anthropic.PermissionDeniedError:
            return ModelCheck(["LT_ANTHROPIC_API_KEY was rejected by the provider"])
        except anthropic.APIStatusError as exc:
            return ModelCheck([_unreachable(str(exc.status_code))])
        except anthropic.APIConnectionError:
            return ModelCheck([_unreachable("connection failed")])

    problems: list[str] = []
    warnings: list[str] = []
    for setting, model_id in models_by_setting.items():
        info = infos[model_id]
        name = f"{setting}={model_id}"
        if info is None:
            problems.append(f"{name}: model not found")
            continue
        lifecycle = getattr(info, "lifecycle", "active")
        if lifecycle == "retired":
            problems.append(f"{name}: model is retired")
            continue
        if lifecycle == "deprecated":
            warnings.append(f"{name}: model is deprecated")
        caps = _capabilities(info)
        if not _supported(caps, "structured_outputs"):
            problems.append(f"{name}: model does not support structured outputs")
        if setting == SEARCH_SETTING and not _supported(caps, "server_tools", "web_search"):
            problems.append(f"{name}: model does not support the web search tool")

    known = {model_id: _capabilities(info) for model_id, info in infos.items() if info}
    tuning = ModelTuning(
        thinking_off=frozenset(
            m for m, caps in known.items() if _supported(caps, "thinking", "types", "disabled")
        ),
        effort_low=frozenset(m for m, caps in known.items() if _supported(caps, "effort", "low")),
    )
    return ModelCheck(problems, warnings, tuning)


def _unreachable(why: str) -> str:
    return f"could not reach the provider to check models ({why}); try again"


def _capabilities(info: Any) -> dict[str, Any]:
    caps = getattr(info, "capabilities", None)
    if caps is None:
        return {}
    if hasattr(caps, "model_dump"):
        caps = caps.model_dump()
    return caps if isinstance(caps, dict) else {}


def _supported(caps: Mapping[str, Any], *path: str) -> bool:
    node: Any = caps
    for key in path:
        node = node.get(key) if isinstance(node, Mapping) else None
    return isinstance(node, Mapping) and node.get("supported") is True
