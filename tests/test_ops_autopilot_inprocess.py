"""Unit tests for the in-process ops-autopilot schedule hook handler."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mindroom.ops_autopilot.inprocess import (
    APPROVER,
    AUTOPILOT_TASK_ID,
    PORTAL_ROOM_ID,
    SUGGESTED_ACTION_TOOL,
    _build_registry,
    run_autopilot_in_process,
)


def _ctx(*, task_id: str = AUTOPILOT_TASK_ID) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        room_id=PORTAL_ROOM_ID,
        logger=MagicMock(),
        suppress=False,
    )


@pytest.mark.asyncio
async def test_ignores_non_autopilot_task() -> None:
    """The handler must not act on unrelated scheduled tasks."""
    ctx = _ctx(task_id="some-other-task")
    with patch("mindroom.ops_autopilot.inprocess._build_registry") as mock_reg:
        await run_autopilot_in_process(ctx)
    mock_reg.assert_not_called()
    assert ctx.suppress is False


@pytest.mark.asyncio
async def test_delivers_brief_ungated_and_gates_suggested_action() -> None:
    """The autopilot task delivers a brief UNGATED and gates one suggested action."""
    ctx = _ctx()

    receipt = SimpleNamespace(ok=True, event_id="$brief", error=None)
    deliverer = MagicMock()
    deliverer.deliver.return_value = receipt

    outcome = SimpleNamespace(status="approved", approved=True, live=True)
    gate = MagicMock()
    gate.gate = AsyncMock(return_value=outcome)

    with (
        patch("mindroom.ops_autopilot.inprocess._build_registry") as mock_reg,
        patch("mindroom.ops_autopilot.inprocess.TelegramDeliverer", return_value=deliverer) as mock_deliverer,
        patch("mindroom.ops_autopilot.inprocess.ApprovalGate", return_value=gate) as mock_gate,
    ):
        await run_autopilot_in_process(ctx)

    # Registry built and run.
    mock_reg.assert_called_once()
    # Brief delivered UNGATED to the portal room.
    mock_deliverer.assert_called_once_with(room_id=PORTAL_ROOM_ID)
    deliverer.deliver.assert_called_once()
    # One ARIP-gated suggested action with the operator approver.
    mock_gate.assert_called_once_with(tool_name=SUGGESTED_ACTION_TOOL, approver=APPROVER)
    gate.gate.assert_awaited_once()
    # The scheduler's default synthetic message is suppressed.
    assert ctx.suppress is True


@pytest.mark.asyncio
async def test_delivery_failure_is_fail_soft() -> None:
    """A failed brief delivery must not raise; the gate still runs."""
    ctx = _ctx()

    receipt = SimpleNamespace(ok=False, event_id=None, error="boom")
    deliverer = MagicMock()
    deliverer.deliver.return_value = receipt

    outcome = SimpleNamespace(status="denied", approved=False, live=True)
    gate = MagicMock()
    gate.gate = AsyncMock(return_value=outcome)

    with (
        patch("mindroom.ops_autopilot.inprocess._build_registry"),
        patch("mindroom.ops_autopilot.inprocess.TelegramDeliverer", return_value=deliverer),
        patch("mindroom.ops_autopilot.inprocess.ApprovalGate", return_value=gate),
    ):
        await run_autopilot_in_process(ctx)

    gate.gate.assert_awaited_once()
    assert ctx.suppress is True


def test_build_registry_includes_all_sources() -> None:
    """The in-process registry includes git, scheduler, mail, and calendar."""
    registry = _build_registry()
    sources = [c.name for c in registry._collectors]
    assert sources == ["git", "scheduler", "mail", "calendar"]