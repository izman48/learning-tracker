"""Spend caps: reserve before every paid call, settle after, on the database clock.

Every test runs against real Postgres, because the guarantees (atomic conditional
updates, a fixed lock order, no deadlock under concurrency) are database behaviour.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

import anyio
import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from learning_tracker.db.engine import make_engine, make_session_factory
from learning_tracker.db.models import SpendLedger, SpendPeriod, SpendState, User
from learning_tracker.worker.spend import AlreadySettled, CapReached, Caps, Reservation, SpendGuard

pytestmark = pytest.mark.anyio
ROOMY = Caps(instance_month_micros=10_000_000, user_month_micros=1_000_000, user_day_micros=100_000)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class World:
    sessions: async_sessionmaker[AsyncSession]

    async def user(self, email: str = "a@example.com", tz: str = "UTC") -> uuid.UUID:
        async with self.sessions() as db:
            user_id = (
                await db.execute(
                    insert(User)
                    .values(email=email, hashed_password="x", timezone=tz)
                    .returning(User.id)
                )
            ).scalar_one()
            await db.commit()
        return user_id

    async def used(self, scope_key: str, kind: str) -> int:
        async with self.sessions() as db:
            rows = (
                (
                    await db.execute(
                        select(SpendPeriod.used_micros).where(
                            SpendPeriod.scope_key == scope_key, SpendPeriod.period_kind == kind
                        )
                    )
                )
                .scalars()
                .all()
            )
        return sum(rows)

    async def ledger(self) -> list[SpendLedger]:
        async with self.sessions() as db:
            return list((await db.execute(select(SpendLedger))).scalars().all())


@pytest.fixture
async def world(migrated_database_url: str) -> AsyncIterator[World]:
    engine = make_engine(migrated_database_url)
    yield World(make_session_factory(engine))
    await engine.dispose()


def guard(world: World, caps: Caps = ROOMY) -> SpendGuard:
    return SpendGuard(world.sessions, caps)


async def reserve(g: SpendGuard, user_id: uuid.UUID, micros: int, tz: str = "UTC") -> Reservation:
    return await g.reserve(
        user_id=user_id, timezone=tz, step="screen", model="claude-haiku-5-5", micros=micros
    )


async def test_a_reservation_counts_against_all_three_caps_and_is_in_the_ledger(
    world: World,
) -> None:
    alice = await world.user()
    r = await reserve(guard(world), alice, 500)
    assert await world.used("instance", "month") == 500
    assert await world.used(f"user:{alice}", "month") == 500
    assert await world.used(f"user:{alice}", "day") == 500
    (row,) = await world.ledger()
    assert (row.id, row.state, row.reserved_micros, row.scope_user_id) == (
        r.ledger_id, SpendState.RESERVED, 500, alice,
    )  # fmt: skip


@pytest.mark.parametrize(
    "caps,scope",
    [
        (Caps(999, 1_000_000, 100_000), "instance monthly"),
        (Caps(10_000_000, 999, 100_000), "user monthly"),
        (Caps(10_000_000, 1_000_000, 999), "user daily"),
    ],
)
async def test_each_cap_blocks_on_its_own_and_nothing_is_half_applied(
    world: World, caps: Caps, scope: str
) -> None:
    alice = await world.user()
    await reserve(guard(world, caps), alice, 999)  # precondition: exactly at the cap is allowed
    with pytest.raises(CapReached) as info:
        await reserve(guard(world, caps), alice, 1)
    assert info.value.scope == scope
    assert await world.used("instance", "month") == 999  # the refused call added nothing
    assert len(await world.ledger()) == 1


async def test_a_zero_cap_pauses_every_paid_call(world: World) -> None:
    alice = await world.user()
    with pytest.raises(CapReached):
        await reserve(guard(world, Caps(0, 1_000_000, 100_000)), alice, 1)
    assert await world.ledger() == []


async def test_settling_below_the_reservation_refunds_the_difference(world: World) -> None:
    alice = await world.user()
    g = guard(world)
    r = await reserve(g, alice, 500)
    assert await g.settle(r, actual_micros=200) == SpendState.SETTLED
    for key, kind in [("instance", "month"), (f"user:{alice}", "month"), (f"user:{alice}", "day")]:
        assert await world.used(key, kind) == 200
    (row,) = await world.ledger()
    assert (row.actual_micros, row.state, row.settled_at is not None) == (200, "settled", True)


async def test_an_overage_is_recorded_past_the_cap_and_the_next_reservation_fails(
    world: World,
) -> None:
    alice = await world.user()
    g = guard(world, Caps(1_000, 1_000_000, 100_000))
    r = await reserve(g, alice, 900)
    assert await g.settle(r, actual_micros=1_200) == SpendState.OVERAGE
    assert await world.used("instance", "month") == 1_200  # past the cap of 1,000
    with pytest.raises(CapReached):
        await reserve(g, alice, 1)


async def test_an_unknown_outcome_keeps_the_full_reservation(world: World) -> None:
    alice = await world.user()
    g = guard(world)
    r = await reserve(g, alice, 500)
    await g.settle_full(r)
    assert await world.used("instance", "month") == 500
    (row,) = await world.ledger()
    assert (row.state, row.actual_micros) == ("settled_full", 500)


async def test_a_reservation_cannot_be_settled_twice(world: World) -> None:
    alice = await world.user()
    g = guard(world)
    r = await reserve(g, alice, 500)
    await g.settle(r, actual_micros=100)
    with pytest.raises(AlreadySettled):
        await g.settle(r, actual_micros=100)
    with pytest.raises(AlreadySettled):
        await g.settle_full(r)
    assert await world.used("instance", "month") == 100


async def test_negative_amounts_are_refused(world: World) -> None:
    alice = await world.user()
    g = guard(world)
    with pytest.raises(ValueError):
        await reserve(g, alice, -1)
    r = await reserve(g, alice, 10)
    with pytest.raises(ValueError):
        await g.settle(r, actual_micros=-1)


async def test_the_daily_cap_follows_each_users_own_date_on_the_database_clock(
    world: World,
) -> None:
    # UTC+14 and UTC-11 are on different calendar days for at least 22 hours of every day.
    east = await world.user("east@example.com", "Pacific/Kiritimati")
    west = await world.user("west@example.com", "Pacific/Pago_Pago")
    g = guard(world)
    await reserve(g, east, 10, "Pacific/Kiritimati")
    await reserve(g, west, 10, "Pacific/Pago_Pago")
    async with world.sessions() as db:
        local_date = text("SELECT (now() AT TIME ZONE :tz)::date")
        expected = {
            tz: (await db.execute(local_date, {"tz": tz})).scalar_one()
            for tz in ("Pacific/Kiritimati", "Pacific/Pago_Pago")
        }
        days = dict(
            (
                await db.execute(
                    select(SpendPeriod.scope_key, SpendPeriod.period_start).where(
                        SpendPeriod.period_kind == "day"
                    )
                )
            ).all()
        )
    assert days[f"user:{east}"] == expected["Pacific/Kiritimati"]
    assert days[f"user:{west}"] == expected["Pacific/Pago_Pago"]
    assert isinstance(days[f"user:{east}"], date)


async def test_twenty_concurrent_reservations_never_exceed_the_cap_or_deadlock(
    world: World,
) -> None:
    # sec: rows created with INSERT ... ON CONFLICT DO NOTHING and locked in a fixed
    # order (instance, user-month, user-day), so racing reservations never overrun.
    users = [await world.user(f"u{i}@example.com") for i in range(4)]
    g = guard(world, Caps(1_000, 1_000_000, 100_000))
    outcomes: list[str] = []

    async def attempt(i: int) -> None:
        try:
            await reserve(g, users[i % 4], 100)
            outcomes.append("ok")
        except CapReached:
            outcomes.append("capped")

    with anyio.fail_after(30):  # a deadlock would hang here
        async with anyio.create_task_group() as group:
            for i in range(20):
                group.start_soon(attempt, i)

    assert outcomes.count("ok") == 10 and outcomes.count("capped") == 10
    assert await world.used("instance", "month") == 1_000
    assert len(await world.ledger()) == 10
