"""Register the P8 ops-autopilot daily 07:30 schedule as real Matrix state.

Writes the canonical `com.mindroom.scheduled.task` room-state event so that
`!list_schedules` and restart restore (`restore_scheduled_tasks`) both see it.
This is the *actual* registration path — the `~/.mindroom/ops_autopilot_schedule.json`
sidecar is only the collector's display hint, not the source of truth.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime

import yaml

from mindroom.scheduling import CronSchedule, ScheduledWorkflow

ROOM_ID = "!dMnnRiMXoHdqYOVtqJ:localhost"  # Personal room (router-managed)
TASK_ID = "ops-autopilot-ba0760ba"
EVENT_TYPE = "com.mindroom.scheduled.task"
BASE = "http://127.0.0.1:8008"

_STATE = yaml.safe_load(
    __import__("pathlib").Path.home().joinpath(".mindroom/mindroom_data/matrix_state.yaml").read_text()
)
_ROUTER = _STATE["accounts"]["agent_router"]


def _content() -> dict:
    workflow = ScheduledWorkflow(
        schedule_type="cron",
        cron_schedule=CronSchedule(minute="30", hour="7", day="*", month="*", weekday="*"),
        message=(
            "Run the Personal Ops Autopilot pipeline daily at 07:30. Collect git and scheduler "
            "signals, compose the ops brief, run it through the approval gate, and deliver it "
            "to Telegram via the Matrix portal bridge."
        ),
        description="Personal Ops Autopilot daily 07:30 brief",
        history_limit=0,
        created_by="@dwayne:localhost",
        thread_id=None,
        room_id=ROOM_ID,
        new_thread=False,
    )
    return {
        "task_id": TASK_ID,
        "workflow": workflow.model_dump_json(),
        "cron_description": workflow.cron_schedule.to_natural_language(),
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _put_room_state(room_id: str, event_type: str, state_key: str, content: dict) -> dict:
    url = (
        f"{BASE}/_matrix/client/v3/rooms/{urllib.parse.quote(room_id, safe='')}"
        f"/state/{urllib.parse.quote(event_type, safe='')}/{urllib.parse.quote(state_key, safe='')}"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(content).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_ROUTER['access_token']}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - fixed loopback
        return json.load(r)


def main() -> None:
    result = _put_room_state(ROOM_ID, EVENT_TYPE, TASK_ID, _content())
    print("PUT state event ->", json.dumps(result, indent=2))


asyncio.run(asyncio.to_thread(main))