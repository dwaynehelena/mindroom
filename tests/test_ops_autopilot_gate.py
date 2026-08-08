"""Unit tests for the ops-autopilot ARIP approval gate."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mindroom.ops_autopilot.approval.gate import ApprovalGate, ApprovalOutcome, request_approval


@pytest.mark.asyncio
async def test_gate_auto_approves_when_no_live_store() -> None:
    # No module-level approval manager wired -> auto-approve fallback.
    with patch("mindroom.ops_autopilot.approval.gate.get_approval_store", return_value=None):
        gate = ApprovalGate()
        outcome = await gate.gate("brief")
    assert outcome.approved is True
    assert outcome.status == "auto_approve"
    assert outcome.live is False


@pytest.mark.asyncio
async def test_gate_approves_via_live_store() -> None:
    store = AsyncMock()
    store.request_approval.return_value = SimpleNamespace(
        status="approved",
        reason=None,
        resolved_by="@dwayne:localhost",
    )
    with patch("mindroom.ops_autopilot.approval.gate.get_approval_store", return_value=store):
        gate = ApprovalGate()
        outcome = await gate.gate("brief", room_id="!r:localhost")
    assert outcome.approved is True
    assert outcome.live is True
    assert outcome.resolved_by == "@dwayne:localhost"
    # Verify request_approval was invoked with the expected tool/approver.
    kwargs = store.request_approval.await_args.kwargs
    assert kwargs["tool_name"] == "ops_autopilot.deliver_brief"
    assert kwargs["approver_user_id"] == "@dwayne:localhost"
    assert kwargs["room_id"] == "!r:localhost"
    assert kwargs["arguments"] == {"brief_length": len("brief"), "target": "telegram_dm"}


@pytest.mark.asyncio
async def test_gate_deny_path_blocks_execution() -> None:
    store = AsyncMock()
    store.request_approval.return_value = SimpleNamespace(
        status="denied",
        reason="operator declined",
        resolved_by="@dwayne:localhost",
    )
    with patch("mindroom.ops_autopilot.approval.gate.get_approval_store", return_value=store):
        gate = ApprovalGate()
        outcome = await gate.gate("brief", room_id="!r:localhost")
    assert outcome.approved is False
    assert outcome.status == "denied"
    assert outcome.live is True
    assert outcome.reason == "operator declined"


@pytest.mark.asyncio
async def test_gate_expired_status_is_not_approved() -> None:
    store = AsyncMock()
    store.request_approval.return_value = SimpleNamespace(status="expired", reason="timed out", resolved_by=None)
    with patch("mindroom.ops_autopilot.approval.gate.get_approval_store", return_value=store):
        gate = ApprovalGate()
        outcome = await gate.gate("brief")
    assert outcome.approved is False
    assert outcome.status == "expired"


@pytest.mark.asyncio
async def test_gate_passes_brief_length_argument() -> None:
    store = AsyncMock()
    store.request_approval.return_value = SimpleNamespace(status="approved", reason=None, resolved_by=None)
    with patch("mindroom.ops_autopilot.approval.gate.get_approval_store", return_value=store):
        await ApprovalGate().gate("some brief content")
    args = store.request_approval.await_args.kwargs["arguments"]
    assert args["brief_length"] == len("some brief content")


def test_approval_outcome_defaults() -> None:
    o = ApprovalOutcome(approved=False, status="denied")
    assert o.reason is None
    assert o.live is False
    assert o.resolved_by is None


def test_request_approval_sync_wrapper() -> None:
    # The synchronous wrapper runs the gate coroutine.
    with (
        patch("mindroom.ops_autopilot.approval.gate.get_approval_store", return_value=None),
        patch(
            "mindroom.ops_autopilot.approval.gate.asyncio.run",
            return_value=ApprovalOutcome(True, "auto_approve"),
        ),
    ):
        outcome = request_approval("brief")
    assert outcome.approved is True