"""Tests for owned Personal Ops daily scheduler lifecycle."""

# ruff: noqa: ANN001, ANN202, D103, EM101, TRY003

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from mindroom.personal_ops import PersonalOpsError
from mindroom.personal_ops_scheduler import (
    PERSONAL_OPS_DAILY_JOB_ID,
    PersonalOpsDailySchedule,
    PersonalOpsDailyScheduler,
    _next_run,
)

if TYPE_CHECKING:
    from mindroom.personal_ops_runtime import PersonalOpsBriefRunner

NOW = datetime(2026, 7, 18, 0, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


class _Runner:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.calls = []
        self.called = asyncio.Event()
        self._fail_once = fail_once

    async def run(self, observed_at):
        self.calls.append(observed_at)
        self.called.set()
        if self._fail_once:
            self._fail_once = False
            raise RuntimeError("private failure")


class _Sleeper:
    def __init__(self) -> None:
        self.delays = []
        self._first = True
        self.blocked = asyncio.Event()

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if self._first:
            self._first = False
            return
        self.blocked.set()
        await asyncio.Event().wait()


def _scheduler(runner, sleeper) -> PersonalOpsDailyScheduler:
    return PersonalOpsDailyScheduler(
        runner=cast("PersonalOpsBriefRunner", runner),
        clock=lambda: NOW,
        sleeper=sleeper,
    )


async def test_daily_schedule_uses_iana_local_time() -> None:
    schedule = PersonalOpsDailySchedule("Australia/Sydney", hour=8)
    assert _next_run(NOW, schedule) == datetime(2026, 7, 18, 22, tzinfo=UTC)


@pytest.mark.parametrize(
    "schedule",
    [
        ("Not/AZone", 8, 0),
        ("UTC", 24, 0),
        ("UTC", 8, 60),
    ],
)
async def test_invalid_schedule_fails_before_task_creation(schedule) -> None:
    with pytest.raises(PersonalOpsError):
        PersonalOpsDailySchedule(schedule[0], schedule[1], schedule[2])


async def test_start_is_idempotent_and_reload_replaces_owned_task() -> None:
    first_runner = _Runner()
    sleeper = _Sleeper()
    scheduler = _scheduler(first_runner, sleeper)
    schedule = PersonalOpsDailySchedule("UTC", 8)
    await scheduler.start(schedule)
    await first_runner.called.wait()
    first_task = scheduler._task
    await scheduler.start(schedule)
    assert scheduler._task is first_task
    await scheduler.start(PersonalOpsDailySchedule("UTC", 9))
    assert scheduler._task is not first_task
    assert first_task is not None
    assert first_task.cancelled()
    await scheduler.shutdown()


async def test_failure_is_isolated_and_loop_continues(monkeypatch) -> None:
    runner = _Runner(fail_once=True)
    sleeper = _Sleeper()
    errors = []
    monkeypatch.setattr(
        "mindroom.personal_ops_scheduler.logger.error",
        lambda event, **kwargs: errors.append((event, kwargs)),
    )
    scheduler = _scheduler(runner, sleeper)
    await scheduler.start(PersonalOpsDailySchedule("UTC", 8))
    await sleeper.blocked.wait()
    assert len(runner.calls) == 1
    assert errors[0][0] == "personal_ops_daily_brief_failed"
    assert "private failure" not in str(errors)
    await scheduler.shutdown()


async def test_shutdown_cancels_only_owned_stable_job() -> None:
    runner = _Runner()
    sleeper = _Sleeper()
    scheduler = _scheduler(runner, sleeper)
    unrelated_gate = asyncio.Event()
    unrelated = asyncio.create_task(unrelated_gate.wait(), name="unrelated")
    await scheduler.start(PersonalOpsDailySchedule("UTC", 8))
    await runner.called.wait()
    assert scheduler.job_id == PERSONAL_OPS_DAILY_JOB_ID
    await scheduler.shutdown()
    assert unrelated.done() is False
    unrelated.cancel()
    with pytest.raises(asyncio.CancelledError):
        await unrelated
