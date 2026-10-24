"""A ModelClient for tests: replies from a queue and records every call.

Pipeline tests use it instead of a mock HTTP transport, so they read as "the model
said X" and never need a key or the network.
"""

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from learning_tracker.worker.models.client import (
    ModelError,
    ModelResult,
    Step,
    UntrustedBlock,
    Usage,
)


@dataclass(frozen=True)
class FakeCall:
    step: Step
    model: str
    system: str
    data: Sequence[UntrustedBlock]
    schema: type[BaseModel]
    max_output_tokens: int


class FakeModelClient:
    def __init__(
        self, replies: Iterable[BaseModel | ModelError], *, usage: Usage | None = None
    ) -> None:
        self._replies: deque[BaseModel | ModelError] = deque(replies)
        self._usage = usage or Usage(input_tokens=100, output_tokens=10)
        self.calls: list[FakeCall] = []

    async def complete_json[T: BaseModel](
        self,
        *,
        step: Step,
        model: str,
        system: str,
        data: Sequence[UntrustedBlock],
        schema: type[T],
        max_output_tokens: int,
    ) -> ModelResult[T]:
        self.calls.append(FakeCall(step, model, system, data, schema, max_output_tokens))
        if not self._replies:
            raise AssertionError("no reply queued for this model call")
        reply = self._replies.popleft()
        if isinstance(reply, ModelError):
            raise reply
        if not isinstance(reply, schema):
            raise TypeError(f"queued {type(reply).__name__} but the call expects {schema.__name__}")
        return ModelResult(parsed=reply, usage=self._usage, model=model)
