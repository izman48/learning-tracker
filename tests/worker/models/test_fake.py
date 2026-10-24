"""The fake model client used by pipeline tests: replies from a queue, records calls."""

import pytest
from pydantic import BaseModel

from learning_tracker.worker.models.client import (
    ModelClient,
    ModelRateLimited,
    Step,
    UntrustedBlock,
    Usage,
)
from learning_tracker.worker.models.fake import FakeModelClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Verdict(BaseModel):
    keep: bool


class Other(BaseModel):
    note: str


async def ask(client: ModelClient, schema: type[BaseModel] = Verdict) -> object:
    return await client.complete_json(
        step=Step.SCREEN, model="claude-haiku-5-5", system="judge",
        data=[UntrustedBlock("page", "text")], schema=schema, max_output_tokens=50,
    )  # fmt: skip


async def test_replies_in_order_and_records_each_call() -> None:
    fake = FakeModelClient([Verdict(keep=True), ModelRateLimited(3.0)], usage=Usage(10, 2))
    first = await ask(fake)
    assert first.parsed == Verdict(keep=True) and first.usage == Usage(10, 2)  # type: ignore[attr-defined]
    with pytest.raises(ModelRateLimited):
        await ask(fake)
    assert [(c.step, c.model, c.data[0].text) for c in fake.calls] == [
        (Step.SCREEN, "claude-haiku-5-5", "text"),
        (Step.SCREEN, "claude-haiku-5-5", "text"),
    ]


async def test_a_reply_of_the_wrong_schema_or_an_empty_queue_fails_loudly() -> None:
    with pytest.raises(TypeError, match="Other"):
        await ask(FakeModelClient([Other(note="x")]))
    with pytest.raises(AssertionError, match="no reply queued"):
        await ask(FakeModelClient([]))
