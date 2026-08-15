"""Unit tests for the ops-autopilot scheduler hook (07:30 cron registration)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from mindroom.ops_autopilot.scheduler_hook import (
    AUTOPILOT_CRON,
    AUTOPILOT_TASK_ID,
    _write_state,
    ensure_daily_autopilot,
    register_daily_autopilot,
)


def test_cron_is_0730_local() -> None:
    assert AUTOPILOT_CRON == "30 7 * * *"


def test_write_state_persists_task_id(tmp_path: pytest.TempPathFactory | object, monkeypatch: pytest.MonkeyPatch) -> None:
    import pathlib

    state = pathlib.Path(tmp_path) / "schedule.json"  # type: ignore[arg-type]
    monkeypatch.setattr("mindroom.ops_autopilot.scheduler_hook._SCHEDULE_STATE", state)
    _write_state("abc123")
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["task_id"] == "abc123"
    assert data["registered"] is True
    assert data["cron"] == AUTOPILOT_CRON


def test_write_state_none_marks_unregistered(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    import pathlib

    state = pathlib.Path(tmp_path) / "schedule.json"  # type: ignore[arg-type]
    monkeypatch.setattr("mindroom.ops_autopilot.scheduler_hook._SCHEDULE_STATE", state)
    _write_state(None)
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["task_id"] is None
    assert data["registered"] is False


@pytest.mark.asyncio
async def test_register_daily_autopilot_calls_schedule_task_and_persists(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pathlib

    state = pathlib.Path(tmp_path) / "schedule.json"  # type: ignore[arg-type]
    monkeypatch.setattr("mindroom.ops_autopilot.scheduler_hook._SCHEDULE_STATE", state)

    runtime = AsyncMock()
    with patch(
        "mindroom.ops_autopilot.scheduler_hook.schedule_task",
        new_callable=AsyncMock,
        return_value=("task-123", "✅ Scheduled recurring task: **daily at 07:30**"),
    ) as mock_schedule:
        task_id, response = await register_daily_autopilot(
            runtime,
            room_id="!r:localhost",
            scheduled_by="@dwayne:localhost",
            thread_id=None,
        )

    assert task_id == "task-123"
    assert "07:30" in response
    # Assert schedule_task invoked with the recurring request and correct params.
    kwargs = mock_schedule.await_args.kwargs
    assert kwargs["room_id"] == "!r:localhost"
    assert kwargs["scheduled_by"] == "@dwayne:localhost"
    assert kwargs["new_thread"] is False
    assert kwargs["history_limit"] == 0
    assert "07:30" in kwargs["full_text"]
    assert "30 7 * * *" in kwargs["full_text"]
    # State was written with the returned task id.
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["task_id"] == "task-123"
    assert data["registered"] is True


@pytest.mark.asyncio
async def test_register_daily_autopilot_persists_none_when_schedule_fails(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pathlib

    state = pathlib.Path(tmp_path) / "schedule.json"  # type: ignore[arg-type]
    monkeypatch.setattr("mindroom.ops_autopilot.scheduler_hook._SCHEDULE_STATE", state)

    runtime = AsyncMock()
    with patch(
        "mindroom.ops_autopilot.scheduler_hook.schedule_task",
        new_callable=AsyncMock,
        return_value=(None, "❌ No agents available"),
    ):
        task_id, response = await register_daily_autopilot(
            runtime,
            room_id="!r:localhost",
            scheduled_by="@dwayne:localhost",
        )

    assert task_id is None
    assert response.startswith("❌")
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["registered"] is False


# ---- Daily cadence idempotency (ensure_daily_autopilot) ----------------


@pytest.mark.asyncio
async def test_ensure_returns_existing_when_task_pending_no_duplicate(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the canonical cron is live+pending, ensure must NOT re-register."""
    import pathlib

    state = pathlib.Path(tmp_path) / "schedule.json"  # type: ignore[arg-type]
    monkeypatch.setattr("mindroom.ops_autopilot.scheduler_hook._SCHEDULE_STATE", state)

    runtime = AsyncMock()
    with patch(
        "mindroom.ops_autopilot.scheduler_hook._live_task_status",
        new_callable=AsyncMock,
        return_value="pending",
    ) as mock_status, patch(
        "mindroom.ops_autopilot.scheduler_hook.register_daily_autopilot",
        new_callable=AsyncMock,
    ) as mock_register:
        task_id, response = await ensure_daily_autopilot(
            runtime,
            room_id="!r:localhost",
            scheduled_by="@dwayne:localhost",
        )

    # Returns the canonical id and does NOT re-register (idempotent).
    assert task_id == AUTOPILOT_TASK_ID
    assert "already live" in response
    mock_status.assert_awaited_once()
    mock_register.assert_not_called()
    # State sidecar refreshed to registered.
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["task_id"] == AUTOPILOT_TASK_ID
    assert data["registered"] is True


@pytest.mark.asyncio
async def test_ensure_registers_when_task_missing(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the canonical cron is absent, ensure registers it once."""
    import pathlib

    state = pathlib.Path(tmp_path) / "schedule.json"  # type: ignore[arg-type]
    monkeypatch.setattr("mindroom.ops_autopilot.scheduler_hook._SCHEDULE_STATE", state)

    runtime = AsyncMock()
    with patch(
        "mindroom.ops_autopilot.scheduler_hook._live_task_status",
        new_callable=AsyncMock,
        return_value=None,  # task absent
    ), patch(
        "mindroom.ops_autopilot.scheduler_hook.register_daily_autopilot",
        new_callable=AsyncMock,
        return_value=(AUTOPILOT_TASK_ID, "✅ Scheduled recurring task: **daily at 07:30**"),
    ) as mock_register:
        task_id, response = await ensure_daily_autopilot(
            runtime,
            room_id="!r:localhost",
            scheduled_by="@dwayne:localhost",
        )

    assert task_id == AUTOPILOT_TASK_ID
    assert "07:30" in response
    mock_register.assert_awaited_once_with(
        runtime, room_id="!r:localhost", scheduled_by="@dwayne:localhost", thread_id=None
    )


@pytest.mark.asyncio
async def test_ensure_registers_when_task_status_non_pending(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale/non-pending task is treated as needing re-registration."""
    import pathlib

    state = pathlib.Path(tmp_path) / "schedule.json"  # type: ignore[arg-type]
    monkeypatch.setattr("mindroom.ops_autopilot.scheduler_hook._SCHEDULE_STATE", state)

    runtime = AsyncMock()
    with patch(
        "mindroom.ops_autopilot.scheduler_hook._live_task_status",
        new_callable=AsyncMock,
        return_value="cancelled",
    ), patch(
        "mindroom.ops_autopilot.scheduler_hook.register_daily_autopilot",
        new_callable=AsyncMock,
        return_value=(AUTOPILOT_TASK_ID, "✅ Scheduled recurring task: **daily at 07:30**"),
    ) as mock_register:
        task_id, _ = await ensure_daily_autopilot(
            runtime,
            room_id="!r:localhost",
            scheduled_by="@dwayne:localhost",
        )

    assert task_id == AUTOPILOT_TASK_ID
    mock_register.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_propagates_registration_failure() -> None:
    """A failed registration returns (None, error response) like schedule_task."""
    runtime = AsyncMock()
    with patch(
        "mindroom.ops_autopilot.scheduler_hook._live_task_status",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "mindroom.ops_autopilot.scheduler_hook.register_daily_autopilot",
        new_callable=AsyncMock,
        return_value=(None, "❌ No agents available"),
    ) as mock_register:
        task_id, response = await ensure_daily_autopilot(
            runtime,
            room_id="!r:localhost",
            scheduled_by="@dwayne:localhost",
        )

    assert task_id is None
    assert response.startswith("❌")
    mock_register.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_degrades_state_read_failure_to_reregister() -> None:
    """A state-read failure must degrade to re-register, never double-register.

    ``_live_task_status`` swallows a Matrix read failure (returns ``None``),
    so ``ensure`` treats it like an absent task: exactly one registration
    attempt, never two.
    """
    runtime = AsyncMock()
    with patch(
        "mindroom.scheduling.get_scheduled_task",
        new_callable=AsyncMock,
        side_effect=RuntimeError("matrix state read failed"),
    ), patch(
        "mindroom.ops_autopilot.scheduler_hook.register_daily_autopilot",
        new_callable=AsyncMock,
        return_value=(AUTOPILOT_TASK_ID, "✅ Scheduled recurring task: **daily at 07:30**"),
    ) as mock_register:
        task_id, _ = await ensure_daily_autopilot(
            runtime,
            room_id="!r:localhost",
            scheduled_by="@dwayne:localhost",
        )

    assert task_id == AUTOPILOT_TASK_ID
    mock_register.assert_awaited_once()