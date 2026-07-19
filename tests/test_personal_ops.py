"""Tests for resilient personal-operations briefs and ARIP-gated actions."""

# ruff: noqa: ANN001, ANN201, ANN202, D103, EM101, TRY003

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from mindroom.arip_control import ApprovalControlError, ApprovalControlStore
from mindroom.personal_ops import OpsAction, OpsItem, PersonalOpsAutopilot, PersonalOpsError, PersonalOpsStore

NOW = datetime(2026, 7, 18, 8, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def stores(tmp_path):
    ops = PersonalOpsStore(tmp_path / "ops.db")
    approvals = ApprovalControlStore(tmp_path / "approvals.db")
    await ops.open()
    await approvals.open()
    yield ops, approvals
    await approvals.close()
    await ops.close()


def _readers(*, mail_fails: bool = False):
    async def reader(source, _observed):
        if source == "mail" and mail_fails:
            raise RuntimeError("secret connector detail")
        return [
            OpsItem(
                f"{source}-1",
                source,
                f"{source} summary",
                NOW,
                due_at=NOW - timedelta(minutes=1) if source == "tasks" else None,
                importance=90 if source == "github" else 10,
            ),
        ]

    return {source: (lambda observed, source=source: reader(source, observed)) for source in ("mail", "calendar", "tasks", "github")}


async def _unused_executor(_action, _key):
    return "unused"


async def test_brief_survives_one_connector_and_prioritizes_overdue(stores) -> None:
    ops, approvals = stores
    autopilot = PersonalOpsAutopilot(
        store=ops,
        approval_store=approvals,
        readers=_readers(mail_fails=True),
        executors={"mindroom": _unused_executor, "openclaw": _unused_executor},
    )
    brief = await autopilot.brief(NOW)
    assert brief.items[0].source == "tasks"
    assert next(health for health in brief.sources if health.source == "mail").available is False
    rendered = brief.render()
    assert "Unavailable sources: mail" in rendered
    assert "secret connector detail" not in rendered


async def test_brief_renders_the_configured_local_calendar_date(stores) -> None:
    ops, approvals = stores
    autopilot = PersonalOpsAutopilot(
        store=ops,
        approval_store=approvals,
        readers=_readers(),
        executors={"mindroom": _unused_executor, "openclaw": _unused_executor},
        display_timezone="Australia/Sydney",
    )
    brief = await autopilot.brief(datetime(2026, 7, 17, 23, tzinfo=UTC))
    assert brief.render().startswith("Personal ops brief — 2026-07-18")


async def test_invalid_display_timezone_is_rejected(stores) -> None:
    ops, approvals = stores
    with pytest.raises(PersonalOpsError, match="IANA"):
        PersonalOpsAutopilot(
            store=ops,
            approval_store=approvals,
            readers=_readers(),
            executors={"mindroom": _unused_executor, "openclaw": _unused_executor},
            display_timezone="Not/AZone",
        )


async def test_preferences_require_explicit_attributable_feedback(stores) -> None:
    ops, _approvals = stores
    with pytest.raises(PersonalOpsError, match="explicit feedback"):
        await ops.learn_preference("brief.hour", 8, explicit_feedback_id="", learned_at=NOW)
    await ops.learn_preference("brief.hour", 8, explicit_feedback_id="$event", learned_at=NOW)
    assert await ops.preferences() == {"brief.hour": 8}


async def test_action_requires_matching_single_use_arip_and_passes_idempotency_key(stores) -> None:
    ops, approvals = stores
    seen = []

    async def execute(action, key):
        seen.append((action.action_id, key))
        return "remote-receipt"

    autopilot = PersonalOpsAutopilot(
        store=ops,
        approval_store=approvals,
        readers=_readers(),
        executors={"mindroom": execute, "openclaw": execute},
    )
    action = OpsAction("action-1", "calendar", "calendar.create", {"title": "Review"}, "openclaw", "approval-1", NOW)
    await approvals.request(
        approval_id="approval-1",
        tool_call_event_id="$tool",
        tool_name=action.tool_name,
        arguments=action.arguments,
        eligible_actors=("@owner:test",),
        quorum=1,
        expires_at=NOW + timedelta(minutes=5),
    )
    await approvals.decide(approval_id="approval-1", actor_id="@owner:test", decision="approved", decided_at=NOW)
    assert await autopilot.execute(action, observed_at=NOW) == "remote-receipt"
    assert seen == [("action-1", action.idempotency_key)]
    assert await ops.status("action-1") == "completed"
    with pytest.raises(ApprovalControlError, match="already consumed"):
        await autopilot.execute(action, observed_at=NOW)


async def test_payload_substitution_is_denied_before_executor(stores) -> None:
    ops, approvals = stores
    called = False

    async def execute(_action, _key):
        nonlocal called
        called = True
        return "receipt"

    autopilot = PersonalOpsAutopilot(
        store=ops,
        approval_store=approvals,
        readers=_readers(),
        executors={"mindroom": execute, "openclaw": execute},
    )
    await approvals.request(
        approval_id="approval-1",
        tool_call_event_id="$tool",
        tool_name="gmail.send",
        arguments={"to": "safe@example.test"},
        eligible_actors=("@owner:test",),
        quorum=1,
        expires_at=NOW + timedelta(minutes=5),
    )
    await approvals.decide(approval_id="approval-1", actor_id="@owner:test", decision="approved", decided_at=NOW)
    changed = OpsAction(
        "action-1",
        "mail",
        "gmail.send",
        {"to": "attacker@example.test"},
        "mindroom",
        "approval-1",
        NOW,
    )
    with pytest.raises(ApprovalControlError, match="does not match"):
        await autopilot.execute(changed, observed_at=NOW)
    assert called is False
    assert await ops.status("action-1") == "pending"


async def test_interrupted_external_write_becomes_uncertain_and_is_not_retried(stores) -> None:
    ops, _approvals = stores
    action = OpsAction("action-1", "github", "github.comment", {"issue": 1}, "openclaw", "approval-1", NOW)
    await ops.stage_action(action)
    await ops.begin(action)
    assert await ops.mark_interrupted_uncertain() == 1
    assert await ops.status(action.action_id) == "uncertain"
    with pytest.raises(PersonalOpsError, match="uncertain"):
        await ops.begin(action)
