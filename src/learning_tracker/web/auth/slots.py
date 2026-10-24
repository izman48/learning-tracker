"""A cap on concurrent password hashing.

Each argon2id hash takes 64 MiB and a few hundred milliseconds of CPU. Hashing runs
in worker threads so it doesn't block the event loop, and at most `capacity` run at
once. When every slot is busy a request fails fast with 503 and Retry-After: 1,
instead of queueing until the process runs out of memory.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, status

DEFAULT_SLOTS = 4
BUSY = "Busy. Try again in a moment."


class PasswordSlots:
    def __init__(self, capacity: int = DEFAULT_SLOTS) -> None:
        self.capacity = capacity
        self.in_use = 0  # one event loop per process, so a plain counter is safe

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self.in_use >= self.capacity:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, BUSY, headers={"Retry-After": "1"}
            )
        self.in_use += 1
        try:
            yield
        finally:
            self.in_use -= 1
