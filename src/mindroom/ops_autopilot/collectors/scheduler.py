"""Scheduler signal collector.

Reads the ops-autopilot recurring schedule (the 07:30 local cron) so the brief
can show that the recurring run is actually wired. Prefers the live Matrix
``com.mindroom.scheduled.task`` room state (the canonical registration record
that ``!list_schedules`` and restart restore both read); falls back to the
``~/.mindroom/ops_autopilot_schedule.json`` sidecar for offline/standalone runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult

# Well-known autopilot cron expression used for the recurring brief.
AUTOPILOT_CRON = "30 7 * * *"
_SCHEDULE_STATE = Path.home() / ".mindroom/ops_autopilot_schedule.json"

# Canonical scheduled-task Matrix room state the router writes on registration.
_SCHEDULED_TASK_EVENT_TYPE = "com.mindroom.scheduled.task"
# Default portal/Personal room where the schedule is registered.
_DEFAULT_ROOM_ID = "!dMnnRiMXoHdqYOVtqJ:localhost"


class SchedulerCollector(BaseCollector):
    """Report the recurring 07:30 local schedule state."""

    name = "scheduler"

    def __init__(
        self,
        cron: str = AUTOPILOT_CRON,
        state_path: Path = _SCHEDULE_STATE,
        room_id: str | None = _DEFAULT_ROOM_ID,
        client=None,
    ) -> None:
        self._cron = cron
        self._state_path = state_path
        self._room_id = room_id
        self._client = client

    def collect(self) -> CollectResult:
        data: dict[str, object] = {"cron": self._cron, "task_id": None, "registered": False}
        try:
            # 1) Live Matrix room-state registration is the source of truth.
            live = self._collect_live()
            if live is not None:
                data.update(live)
                return CollectResult(self.name, True, data=data)

            # 2) Fallback to the sidecar for offline/standalone runs.
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

    def _collect_live(self) -> dict[str, object] | None:
        """Query the live Matrix room-state registration record, if a client is wired."""
        if self._client is None or not self._room_id:
            return None
        try:
            import asyncio

            return asyncio.run(self._fetch_live())
        except Exception:
            return None

    async def _fetch_live(self) -> dict[str, object] | None:
        response = await self._client.room_get_state(self._room_id)
        if not hasattr(response, "events"):
            return None
        for event in response.events:
            if event.get("type") != _SCHEDULED_TASK_EVENT_TYPE:
                continue
            content = event.get("content")
            state_key = event.get("state_key")
            if not isinstance(content, dict) or not isinstance(state_key, str):
                continue
            workflow_raw = content.get("workflow")
            task_id = state_key if state_key else content.get("task_id")
            registered = bool(task_id)
            cron = self._cron
            if isinstance(workflow_raw, str):
                try:
                    workflow = json.loads(workflow_raw)
                    cs = workflow.get("cron_schedule") if isinstance(workflow, dict) else None
                    if isinstance(cs, dict):
                        cron = (
                            f"{cs.get('minute')} {cs.get('hour')} {cs.get('day')} "
                            f"{cs.get('month')} {cs.get('weekday')}"
                        ).strip()
                except (ValueError, TypeError):
                    pass
            return {
                "cron": cron,
                "task_id": task_id,
                "registered": registered,
                "live_matrix_state": True,
            }
        return None