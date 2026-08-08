"""Compose collected ops signals into a bounded human-readable test brief."""

from __future__ import annotations

from datetime import UTC, datetime

from mindroom.ops_autopilot.collectors.base import CollectResult
from mindroom.ops_autopilot.collectors.git import GitSummary
from mindroom.ops_autopilot.collectors.scheduler import SchedulerCollector

MAX_BODY_BYTES = 12 * 1024


def _git_block(result: CollectResult) -> list[str]:
    if not result.ok or not isinstance(result.data, GitSummary):
        return ["• git: unavailable"]
    git = result.data
    lines = [
        f"• git: branch {git.branch}",
        f"  • ahead {git.ahead} / behind {git.behind} · {'dirty' if git.dirty else 'clean'}",
    ]
    if git.recent_commits:
        lines.append("  • recent:")
        lines.extend(f"    - {line}" for line in git.recent_commits)
    return lines


def _scheduler_block(result: CollectResult) -> list[str]:
    if not result.ok or not isinstance(result.data, dict):
        return ["• scheduler: unavailable"]
    data = result.data
    cron = data.get("cron") or SchedulerCollector.AUTOPILOT_CRON
    task_id = data.get("task_id")
    registered = "registered" if data.get("registered") else "not registered"
    line = f"• scheduler: cron `{cron}` · {registered}"
    if task_id:
        line += f" · task `{task_id}`"
    return [line]


def compose_brief(results: list[CollectResult]) -> str:
    """Render a bounded brief from ordered collect results."""
    timestamp = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    lines: list[str] = [
        "🧭 Ops Autopilot test brief",
        f"Generated {timestamp}",
        "",
    ]
    for result in results:
        if result.source == "git":
            lines.extend(_git_block(result))
        elif result.source == "scheduler":
            lines.extend(_scheduler_block(result))
        else:
            lines.append(result.render())
    lines.extend(
        [
            "",
            "This is a real end-to-end test brief delivered via the Matrix portal "
            "room bridge to Dwayne's Telegram DM (8411753427).",
        ]
    )
    body = "\n".join(lines)
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        message = "composed brief exceeds byte limit"
        raise ValueError(message)
    return body