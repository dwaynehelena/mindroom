"""Recurring 07:30 local schedule hook for the ops autopilot.

Uses ``schedule_task`` from ``mindroom.scheduling`` to register a recurring
cron that re-runs the autopilot pipeline every day at 07:30 local time, then
persists the resulting task id for the scheduler collector to surface.

Daily cadence layer
-------------------
:func:`ensure_daily_autopilot` is the idempotent entrypoint the daily cadence
relies on. It verifies the canonical cron task (``ops-autopilot-ba0760ba``) is
already registered and healthy in live Matrix room state; if it is, it returns
the existing task id and does NOT create a duplicate. Only when the task is
missing or no longer pending does it (re)register the cron, reusing the same
deterministic task id so a re-registration never produces a second task.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from mindroom.scheduling import SchedulingRuntime, schedule_task

# Local-time cron for the daily 07:30 brief. Config timezone is America/Los_Angeles.
AUTOPILOT_CRON = "30 7 * * *"
# Canonical, deterministic task id reused across registrations so the daily
# cadence is idempotent and restarts never create a duplicate cron.
AUTOPILOT_TASK_ID = "ops-autopilot-ba0760ba"
_SCHEDULE_STATE = Path.home() / ".mindroom/ops_autopilot_schedule.json"

_SCHEDULE_REQUEST = (
    f"Run the Personal Ops Autopilot pipeline daily at 07:30. "
    f"Collect git and scheduler signals, compose the ops brief, run it through "
    f"the approval gate, and deliver it to Telegram via the Matrix portal bridge. "
    f"Cron: {AUTOPILOT_CRON}."
)


def _write_state(task_id: str | None) -> None:
    data: dict[str, Any] = {
        "cron": AUTOPILOT_CRON,
        "task_id": task_id,
        "registered": bool(task_id),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _SCHEDULE_STATE.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def _live_task_status(
    runtime: SchedulingRuntime, room_id: str, task_id: str
) -> str | None:
    """Return the live Matrix status for one scheduled task, or None if absent.

    A missing read degrades to ``None`` (treated as absent) so a transient state
    read failure can never be mistaken for a healthy existing task.
    """
    try:
        from mindroom.scheduling import get_scheduled_task

        task = await get_scheduled_task(
            client=runtime.client, room_id=room_id, task_id=task_id
        )
    except Exception:  # noqa: BLE001 - degraded read must fail open to re-register
        return None
    if task is None:
        return None
    return task.status


async def register_daily_autopilot(
    runtime: SchedulingRuntime,
    *,
    room_id: str,
    scheduled_by: str,
    thread_id: str | None = None,
) -> tuple[str | None, str]:
    """Register (or re-register) the daily 07:30 autopilot schedule.

    Returns ``(task_id, response_message)`` like ``schedule_task``.
    """
    task_id, response = await schedule_task(
        runtime,
        room_id=room_id,
        thread_id=thread_id,
        scheduled_by=scheduled_by,
        full_text=_SCHEDULE_REQUEST,
        new_thread=False,
        history_limit=0,
        task_id=AUTOPILOT_TASK_ID,
    )
    _write_state(task_id)
    return task_id, response


async def ensure_daily_autopilot(
    runtime: SchedulingRuntime,
    *,
    room_id: str,
    scheduled_by: str,
    thread_id: str | None = None,
) -> tuple[str | None, str]:
    """Idempotently ensure the daily 07:30 autopilot cadence is registered.

    Verifies the canonical cron task ``ops-autopilot-ba0760ba`` is present and
    still ``pending`` in live Matrix room state. When it is healthy, returns the
    existing task id and does NOT create a duplicate. Otherwise (re)registers
    the cron with the same deterministic task id.

    Returns ``(task_id, response_message)``:
      - existing task -> ``(<task_id>, "✅ Daily autopilot cadence already live ...")``
      - freshly (re)registered -> ``(<task_id>, <schedule_task response>)``
      - registration failed -> ``(None, <error response>)``
    """
    status = await _live_task_status(runtime, room_id, AUTOPILOT_TASK_ID)
    if status == "pending":
        _write_state(AUTOPILOT_TASK_ID)
        return (
            AUTOPILOT_TASK_ID,
            f"✅ Daily autopilot cadence already live: task `{AUTOPILOT_TASK_ID}` "
            f"(`{AUTOPILOT_CRON}`) is registered and pending. No duplicate created.",
        )

    return await register_daily_autopilot(
        runtime,
        room_id=room_id,
        scheduled_by=scheduled_by,
        thread_id=thread_id,
    )