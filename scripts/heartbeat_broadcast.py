#!/usr/bin/env python3
"""Broadcast a bounded service heartbeat and task report to the Telegram Matrix portal."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import yaml

PORTAL_ROOM_ID = "!kNowWhbQOKJMwCNzqB:localhost"
MATRIX_ORIGIN = "http://127.0.0.1:8008"
STATE_PATH = Path.home() / ".mindroom/mindroom_data/matrix_state.yaml"
TASKS_PATH = Path.home() / ".mindroom/current_tasks.json"
MAX_BODY_BYTES = 12 * 1024


def _get_json(url: str, timeout: float = 3.0) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed loopback URLs only
        return json.load(response)


def _service_status(url: str) -> str:
    try:
        value = _get_json(url)
    except (OSError, ValueError, urllib.error.URLError):
        return "DOWN"
    if isinstance(value, dict):
        status = value.get("status")
        if status in {"ready", "live", "ok"} or value.get("ok") is True:
            return "OK"
    return "DEGRADED"


def _tasks() -> list[dict[str, str]]:
    try:
        value = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [{"status": "unknown", "task": "Task manifest unavailable"}]
    if not isinstance(value, list):
        return [{"status": "unknown", "task": "Task manifest is invalid"}]
    tasks: list[dict[str, str]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task", "")).strip().replace("\n", " ")
        status = str(item.get("status", "unknown")).strip().replace("\n", " ")
        if task:
            tasks.append({"status": status[:32], "task": task[:500]})
    return tasks or [{"status": "idle", "task": "No current tasks"}]


def _body() -> str:
    timestamp = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    lines = [
        f"💓 MindRoom heartbeat — {timestamp}",
        "",
        "Services:",
        f"• MindRoom: {_service_status('http://127.0.0.1:8765/api/ready')}",
        f"• OpenClaw gateway: {_service_status('http://127.0.0.1:18789/health')}",
        f"• Hermes: {_service_status('http://127.0.0.1:8642/health')}",
        "",
        "Current tasks:",
    ]
    lines.extend(f"• [{item['status']}] {item['task']}" for item in _tasks())
    body = "\n".join(lines)
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        message = "heartbeat body exceeds byte limit"
        raise ValueError(message)
    return body


def _access_token() -> str:
    state = yaml.safe_load(STATE_PATH.read_text(encoding="utf-8"))
    token = state.get("accounts", {}).get("agent_user", {}).get("access_token")
    if not isinstance(token, str) or not token:
        message = "canonical Matrix access token is unavailable"
        raise RuntimeError(message)
    return token


def main() -> int:
    """Send one idempotent heartbeat for the current five-minute bucket."""
    body = _body()
    bucket = int(datetime.now(UTC).timestamp()) // 300
    transaction_id = f"mindroom-heartbeat-{bucket}"
    room = urllib.parse.quote(PORTAL_ROOM_ID, safe="")
    txn = urllib.parse.quote(transaction_id, safe="")
    url = f"{MATRIX_ORIGIN}/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}"
    request = urllib.request.Request(  # noqa: S310 - fixed loopback origin only
        url,
        data=json.dumps({"msgtype": "m.text", "body": body}).encode("utf-8"),
        headers={"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed loopback origin only
            result = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"heartbeat broadcast failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    if not isinstance(result, dict) or not isinstance(result.get("event_id"), str):
        print("heartbeat broadcast returned no event ID", file=sys.stderr)
        return 1
    print("heartbeat broadcast delivered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
