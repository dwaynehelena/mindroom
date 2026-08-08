"""Scheduler signal collector.

Reads the ops-autopilot recurring schedule (the 07:30 local cron) so the brief
can show that the recurring run is actually wired, without invoking the full
scheduling runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult

# Well-known autopilot cron expression used for the recurring brief.
AUTOPILOT_CRON = "30 7 * * *"
_SCHEDULE_STATE = Path.home() / ".mindroom/ops_autopilot_schedule.json"


class SchedulerCollector(BaseCollector):
    """Report the recurring 07:30 local schedule state."""

    name = "scheduler"

    def __init__(self, cron: str = AUTOPILOT_CRON, state_path: Path = _SCHEDULE_STATE) -> None:
        self._cron = cron
        self._state_path = state_path

    def collect(self) -> CollectResult:
        try:
            data: dict[str, object] = {"cron": self._cron, "task_id": None, "registered": False}
            if self._state_path.exists():
                try:
                    state = json.loads(self._state_path.read_text(encoding="utf-8"))
                    if isinstance(state, dict):
                        data["task_id"] = state.get("task_id")
                        data["registered"] = bool(state.get("task_id"))
                except (OSError, ValueError):
                    pass
            return CollectResult(self.name, True, data=data)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(self.name, False, error=f"{type(exc).__name__}: {exc}")