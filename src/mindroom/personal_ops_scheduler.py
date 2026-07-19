"""Owned daily scheduler lifecycle for the Personal Ops brief runner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from mindroom.logging_config import get_logger
from mindroom.personal_ops import PersonalOpsError

if TYPE_CHECKING:
    from mindroom.personal_ops_runtime import PersonalOpsBriefRunner

logger = get_logger(__name__)

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
PERSONAL_OPS_DAILY_JOB_ID = "mindroom:personal-ops:daily-brief"


@dataclass(frozen=True, slots=True)
class PersonalOpsDailySchedule:
    """One bounded IANA-local daily execution time."""

    timezone: str
    hour: int
    minute: int = 0

    def __post_init__(self) -> None:
        """Validate timezone and wall-clock fields before task creation."""
        try:
            ZoneInfo(self.timezone)
        except (TypeError, ZoneInfoNotFoundError) as exc:
            message = "personal ops daily schedule requires a valid IANA timezone"
            raise PersonalOpsError(message) from exc
        if self.hour not in range(24) or self.minute not in range(60):
            message = "personal ops daily schedule hour or minute is invalid"
            raise PersonalOpsError(message)


class PersonalOpsDailyScheduler:
    """Own exactly one reloadable daily brief task and no unrelated schedules."""

    def __init__(
        self,
        *,
        runner: PersonalOpsBriefRunner,
        clock: Clock | None = None,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        self._task: asyncio.Task[None] | None = None
        self._schedule: PersonalOpsDailySchedule | None = None
        self._lock = asyncio.Lock()

    @property
    def job_id(self) -> str:
        """Return the stable scheduler identity used across reloads."""
        return PERSONAL_OPS_DAILY_JOB_ID

    async def start(self, schedule: PersonalOpsDailySchedule) -> None:
        """Start once, or atomically replace this owned task when its schedule changes."""
        async with self._lock:
            if self._task is not None and not self._task.done() and self._schedule == schedule:
                return
            await self._cancel_owned_task()
            self._schedule = schedule
            self._task = asyncio.create_task(self._run(schedule), name=PERSONAL_OPS_DAILY_JOB_ID)

    async def shutdown(self) -> None:
        """Cancel and await only the Personal Ops task owned by this scheduler."""
        async with self._lock:
            await self._cancel_owned_task()
            self._schedule = None

    async def _cancel_owned_task(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self, schedule: PersonalOpsDailySchedule) -> None:
        while True:
            observed_at = _utc(self._clock())
            next_run = _next_run(observed_at, schedule)
            await self._sleeper(max(0.0, (next_run - observed_at).total_seconds()))
            fired_at = _utc(self._clock())
            try:
                await self._runner.run(fired_at)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(  # noqa: TRY400 - exception text and traceback may contain private connector data
                    "personal_ops_daily_brief_failed",
                    exception_type=f"{type(exc).__module__}.{type(exc).__qualname__}",
                    job_id=PERSONAL_OPS_DAILY_JOB_ID,
                )


def _next_run(observed_at: datetime, schedule: PersonalOpsDailySchedule) -> datetime:
    local = observed_at.astimezone(ZoneInfo(schedule.timezone))
    expression = f"{schedule.minute} {schedule.hour} * * *"
    return croniter(expression, local).get_next(datetime).astimezone(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "personal ops scheduler clock must return an aware timestamp"
        raise PersonalOpsError(message)
    return value.astimezone(UTC)
