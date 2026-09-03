"""Unit tests for the ops-autopilot brief composer (bounded body)."""

from __future__ import annotations

import pytest

from mindroom.ops_autopilot.collectors.base import CollectResult
from mindroom.ops_autopilot.collectors.git import GitSummary
from mindroom.ops_autopilot.collectors.scheduler import AUTOPILOT_CRON
from mindroom.ops_autopilot.composer import MAX_BODY_BYTES, compose_brief


def _git_ok(**kw) -> CollectResult:
    defaults = dict(branch="main", ahead=1, behind=0, dirty=False, recent_commits=["abc x"])
    defaults.update(kw)
    return CollectResult("git", True, data=GitSummary(**defaults))


def test_compose_brief_renders_git_and_scheduler() -> None:
    body = compose_brief(
        [
            _git_ok(),
            CollectResult(
                "scheduler",
                True,
                data={"cron": AUTOPILOT_CRON, "registered": True, "task_id": "t1"},
            ),
        ],
    )
    assert "Ops Autopilot test brief" in body
    assert "• git: branch main" in body
    assert "• scheduler: cron `30 7 * * *` · registered · task `t1`" in body


def test_compose_brief_git_unavailable_line() -> None:
    body = compose_brief([CollectResult("git", False, error="repo not found")])
    assert "• git: unavailable" in body


def test_compose_brief_scheduler_unregistered_line() -> None:
    body = compose_brief(
        [CollectResult("scheduler", True, data={"cron": AUTOPILOT_CRON, "registered": False, "task_id": None})],
    )
    assert "• scheduler: cron `30 7 * * *` · not registered" in body


def test_compose_brief_unknown_source_uses_render() -> None:
    body = compose_brief([CollectResult("custom", True, data={"x": 1})])
    assert "• custom: OK" in body


def test_compose_brief_renders_deferred_mail_with_evidence() -> None:
    body = compose_brief(
        [
            CollectResult(
                "mail",
                True,
                data={"deferred": True, "reason": "no gmail credentials found"},
            ),
        ],
    )
    assert "• mail: deferred — no gmail credentials found" in body


def test_compose_brief_renders_deferred_calendar_with_evidence() -> None:
    body = compose_brief(
        [
            CollectResult(
                "calendar",
                True,
                data={"deferred": True, "reason": "no google_calendar credentials found"},
            ),
        ],
    )
    assert "• calendar: deferred — no google_calendar credentials found" in body


def test_compose_brief_deferred_source_unavailable_when_not_dict() -> None:
    body = compose_brief([CollectResult("mail", True, data="weird")])
    assert "• mail: unavailable" in body


def test_compose_brief_body_within_byte_limit() -> None:
    results = [_git_ok(recent_commits=[f"abc{i} msg" for i in range(5)]) for _ in range(20)]
    body = compose_brief(results)
    assert len(body.encode("utf-8")) <= MAX_BODY_BYTES


def test_compose_brief_raises_when_exceeding_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force a tiny limit to prove the bound is enforced.
    monkeypatch.setattr("mindroom.ops_autopilot.composer.MAX_BODY_BYTES", 32)
    with pytest.raises(ValueError, match="exceeds byte limit"):
        compose_brief([_git_ok()])


def test_compose_brief_scheduler_non_dict_unavailable() -> None:
    body = compose_brief([CollectResult("scheduler", True, data="weird")])
    assert "• scheduler: unavailable" in body


def test_compose_brief_git_non_summary_unavailable() -> None:
    body = compose_brief([CollectResult("git", True, data="not a summary")])
    assert "• git: unavailable" in body


def test_compose_brief_includes_telegram_dm_footer() -> None:
    body = compose_brief([_git_ok()])
    assert "8411753427" in body


def test_compose_brief_deterministic_generated_at() -> None:
    from datetime import UTC, datetime

    fixed = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
    a = compose_brief([_git_ok()], generated_at=fixed)
    b = compose_brief([_git_ok()], generated_at=fixed)
    assert a == b  # deterministic for the same generation instant
    expected = f"Generated {fixed.astimezone().isoformat(timespec='seconds')}"
    assert expected in a