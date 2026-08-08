"""Recurring 07:30 local schedule hook for the ops autopilot.

Uses ``schedule_task`` from ``mindroom.scheduling`` to register a recurring
cron that re-runs the autopilot pipeline every day at 07:30 local time, then
persists the resulting task id for the scheduler collector to surface.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from mindroom.scheduling import SchedulingRuntime, schedule_task

# Local-time cron for the daily 07:30 brief. Config timezone is America/Los_Angeles.
AUTOPILOT_CRON = "30 7 * * *"
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
    )
    _write_state(task_id)
    return task_id, response