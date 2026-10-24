"""Spend caps: reserve before every paid call, settle after it.

Three caps apply to every call: the instance per month, the user per month and the
user per day (their own local date). A reservation adds the call's worst-case cost
to all three in one transaction, each with a conditional UPDATE that only succeeds
while the total stays within its cap; if any of the three refuses, nothing is
recorded and the call must not be made.

- Period rows are created with INSERT ... ON CONFLICT DO NOTHING and then updated in
  a fixed order (instance, user month, user day), so concurrent reservations queue
  on the same rows in the same order and cannot deadlock.
- Periods come from the database's clock, never the worker's.
- Settling adjusts the same period rows the reservation charged (the ledger keeps
  them), even if midnight or the month end has passed since.
- An actual cost above the reservation is added even past the cap ("overage"), which
  makes the next reservation fail. An unknown outcome (timeout, crash) keeps the full
  reservation, because the provider may have billed it.

Amounts are integer micro-dollars.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from learning_tracker.config import Settings
from learning_tracker.db.models import SpendLedger, SpendState

MICROS_PER_USD = 1_000_000

_PERIODS = text(
    """
    SELECT date_trunc('month', now() AT TIME ZONE 'UTC')::date AS month_start,
           (now() AT TIME ZONE :tz)::date AS day_start
    """
)
_ENSURE_PERIODS = text(
    """
    INSERT INTO spend_periods (scope_key, period_kind, period_start, cap_micros)
    VALUES ('instance', 'month', :month, :cap_instance),
           (:user_key, 'month', :month, :cap_user_month),
           (:user_key, 'day', :day, :cap_user_day)
    ON CONFLICT DO NOTHING
    """
)
_CHARGE = text(
    """
    UPDATE spend_periods SET used_micros = used_micros + :micros, cap_micros = :cap
    WHERE scope_key = :key AND period_kind = CAST(:kind AS spend_period_kind)
      AND period_start = :start AND used_micros + :micros <= :cap
    RETURNING 1
    """
)
_ADJUST = text(
    """
    UPDATE spend_periods SET used_micros = GREATEST(used_micros + :delta, 0)
    WHERE scope_key = :key AND period_kind = CAST(:kind AS spend_period_kind)
      AND period_start = :start
    """
)


def usd_to_micros(amount: Decimal) -> int:
    return int((amount * MICROS_PER_USD).to_integral_value())


@dataclass(frozen=True)
class Caps:
    instance_month_micros: int
    user_month_micros: int
    user_day_micros: int

    @classmethod
    def from_settings(cls, settings: Settings) -> Caps:
        return cls(
            usd_to_micros(settings.spend_cap_instance_monthly_usd),
            usd_to_micros(settings.spend_cap_user_monthly_usd),
            usd_to_micros(settings.spend_cap_user_daily_usd),
        )


class CapReached(Exception):
    def __init__(self, scope: str) -> None:
        super().__init__(f"{scope} spend cap reached")
        self.scope = scope


class AlreadySettled(Exception):
    pass


@dataclass(frozen=True)
class Reservation:
    ledger_id: uuid.UUID
    micros: int


class SpendGuard:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], caps: Caps) -> None:
        self._sessions = sessions
        self._caps = caps

    async def reserve(
        self, *, user_id: uuid.UUID, timezone: str, step: str, model: str, micros: int
    ) -> Reservation:
        if micros < 0:
            raise ValueError("a reservation cannot be negative")
        user_key = f"user:{user_id}"
        async with self._sessions() as db, db.begin():
            month, day = (await db.execute(_PERIODS, {"tz": timezone})).one()
            await db.execute(
                _ENSURE_PERIODS,
                {
                    "month": month,
                    "day": day,
                    "user_key": user_key,
                    "cap_instance": self._caps.instance_month_micros,
                    "cap_user_month": self._caps.user_month_micros,
                    "cap_user_day": self._caps.user_day_micros,
                },
            )
            for scope, key, kind, start, cap in (
                ("instance monthly", "instance", "month", month, self._caps.instance_month_micros),
                ("user monthly", user_key, "month", month, self._caps.user_month_micros),
                ("user daily", user_key, "day", day, self._caps.user_day_micros),
            ):
                charged = await db.execute(
                    _CHARGE,
                    {"key": key, "kind": kind, "start": start, "cap": cap, "micros": micros},
                )
                if charged.first() is None:
                    raise CapReached(scope)  # leaving the block rolls everything back
            ledger_id = uuid.uuid7()
            db.add(
                SpendLedger(
                    id=ledger_id,
                    scope_user_id=user_id,
                    step=step,
                    model=model,
                    reserved_micros=micros,
                    state=SpendState.RESERVED,
                    month_start=month,
                    day_start=day,
                )
            )
        return Reservation(ledger_id=ledger_id, micros=micros)

    async def settle(self, reservation: Reservation, *, actual_micros: int) -> SpendState:
        """The provider reported the cost: refund the difference, or record an overage."""
        if actual_micros < 0:
            raise ValueError("an actual cost cannot be negative")
        state = SpendState.OVERAGE if actual_micros > reservation.micros else SpendState.SETTLED
        await self._close(reservation, actual_micros, state)
        return state

    async def settle_full(self, reservation: Reservation) -> None:
        """The outcome is unknown (timeout, connection lost after sending, crash): the
        provider may have billed it, so the reservation stands in full."""
        await self._close(reservation, reservation.micros, SpendState.SETTLED_FULL)

    async def _close(self, reservation: Reservation, actual: int, state: SpendState) -> None:
        async with self._sessions() as db, db.begin():
            row = (
                await db.execute(
                    select(SpendLedger)
                    .where(
                        SpendLedger.id == reservation.ledger_id,
                        SpendLedger.state == SpendState.RESERVED,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise AlreadySettled(str(reservation.ledger_id))
            delta = actual - row.reserved_micros
            if delta:
                await self._adjust(db, row, delta)
            row.actual_micros = actual
            row.state = state
            row.settled_at = func.now()

    @staticmethod
    async def _adjust(db: AsyncSession, row: SpendLedger, delta: int) -> None:
        month: date | None = row.month_start
        day: date | None = row.day_start
        user_key = f"user:{row.scope_user_id}"
        for key, kind, start in (
            ("instance", "month", month),
            (user_key, "month", month),
            (user_key, "day", day),
        ):
            await db.execute(_ADJUST, {"key": key, "kind": kind, "start": start, "delta": delta})
