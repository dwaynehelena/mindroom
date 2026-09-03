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


def _deferred_block(result: CollectResult) -> list[str]:
    """Render a fail-soft mail/calendar source with its evidence."""
    if not result.ok:
        return [f"• {result.source}: unavailable — {result.error or 'collect failed'}"]
    if not isinstance(result.data, dict):
        return [f"• {result.source}: unavailable"]
    data = result.data
    reason = data.get("reason")
    line = f"• {result.source}: deferred"
    if reason:
        line += f" — {reason}"
    return [line]


def compose_brief(results: list[CollectResult], *, generated_at: datetime | None = None) -> str:
    """Render a bounded brief from ordered collect results.

    ``generated_at`` may be injected for deterministic output in tests/replay;
    otherwise it defaults to the current UTC wall clock (justified by the
    collector timestamps in each ``CollectResult`` so the brief's own generation
    time is always comparable to its sources).
    """
    if generated_at is None:
        generated_at = datetime.now(UTC)
    timestamp = generated_at.astimezone().isoformat(timespec="seconds")
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
        elif result.source in ("mail", "calendar"):
            lines.extend(_deferred_block(result))
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