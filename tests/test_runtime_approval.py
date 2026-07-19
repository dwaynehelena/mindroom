"""Tests for exact-payload OpenClaw and Hermes ARIP adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from mindroom.approval_manager import ApprovalDecision
from mindroom.arip_control import ApprovalControlError, ApprovalControlStore
from mindroom.runtime_approval import (
    HermesApprovalAdapter,
    OpenClawApprovalAdapter,
    RuntimeAction,
    RuntimeApprovalError,
    RuntimeName,
)

if TYPE_CHECKING:
    from pathlib import Path


async def _approved_store(path: Path, action: RuntimeAction, now: datetime) -> ApprovalControlStore:
    store = ApprovalControlStore(path)
    await store.open()
    await store.request(
        approval_id=action.approval_id,
        tool_call_event_id=f"tool:{action.action_id}",
        tool_name=action.approval_tool_name,
        arguments=action.approval_arguments,
        eligible_actors=("@owner:example.com",),
        quorum=1,
        expires_at=now + timedelta(minutes=5),
    )
    await store.decide(
        approval_id=action.approval_id,
        actor_id="@owner:example.com",
        decision="approved",
        decided_at=now,
    )
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime", "adapter_type"),
    [("openclaw", OpenClawApprovalAdapter), ("hermes", HermesApprovalAdapter)],
)
async def test_runtime_adapter_consumes_exact_envelope_once(
    tmp_path: Path,
    runtime: RuntimeName,
    adapter_type: type[OpenClawApprovalAdapter | HermesApprovalAdapter],
) -> None:
    """Each adapter consumes one exact envelope and cannot execute it twice."""
    now = datetime.now(UTC)
    action = RuntimeAction("action-1", runtime, "calendar.create", {"title": "Review"}, "approval-1")
    store = await _approved_store(tmp_path / "arip.db", action, now)
    calls: list[tuple[RuntimeAction, str]] = []

    async def execute(candidate: RuntimeAction, key: str) -> str:
        calls.append((candidate, key))
        return "receipt-1"

    try:
        adapter = adapter_type(store, execute)
        assert await adapter.execute(action, observed_at=now) == "receipt-1"
        assert calls == [(action, action.idempotency_key)]
        with pytest.raises(ApprovalControlError, match="already consumed"):
            await adapter.execute(action, observed_at=now)
        assert len(calls) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_runtime_substitution_is_denied_before_executor(tmp_path: Path) -> None:
    """An approval for one runtime must never authorize another runtime."""
    now = datetime.now(UTC)
    action = RuntimeAction("action-1", "openclaw", "mail.send", {"to": "a@example.com"}, "approval-1")
    store = await _approved_store(tmp_path / "arip.db", action, now)
    called = False

    async def execute(_candidate: RuntimeAction, _key: str) -> str:
        nonlocal called
        called = True
        return "receipt"

    try:
        adapter = HermesApprovalAdapter(store, execute)
        with pytest.raises(RuntimeApprovalError, match="does not match"):
            await adapter.execute(action, observed_at=now)
        assert called is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_payload_mutation_is_denied_before_executor(tmp_path: Path) -> None:
    """Any mutation of approved arguments must fail before execution."""
    now = datetime.now(UTC)
    approved = RuntimeAction("action-1", "openclaw", "mail.send", {"to": "a@example.com"}, "approval-1")
    mutated = RuntimeAction("action-1", "openclaw", "mail.send", {"to": "b@example.com"}, "approval-1")
    store = await _approved_store(tmp_path / "arip.db", approved, now)
    called = False

    async def execute(_candidate: RuntimeAction, _key: str) -> str:
        nonlocal called
        called = True
        return "receipt"

    try:
        with pytest.raises(ApprovalControlError, match="does not match"):
            await OpenClawApprovalAdapter(store, execute).execute(mutated, observed_at=now)
        assert called is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_live_adapter_uses_namespaced_exact_envelope_before_execution(tmp_path: Path) -> None:
    """Live Matrix/Telegram approval must bind runtime, operation, action and arguments."""
    store = ApprovalControlStore(tmp_path / "unused.db")
    await store.open()
    manager = AsyncMock()
    manager.request_approval.return_value = ApprovalDecision(
        status="approved",
        reason=None,
        resolved_by="@owner:example.com",
        resolved_at=datetime.now(UTC),
    )
    calls: list[RuntimeAction] = []

    async def execute(action: RuntimeAction, _key: str) -> str:
        calls.append(action)
        return "runtime-receipt"

    try:
        adapter = HermesApprovalAdapter(store, execute, manager)
        receipt = await adapter.request_and_execute(
            action_id="action-live",
            operation="calendar.create",
            arguments={"title": "Review"},
            room_id="!room:example.com",
            requester_id="@owner:example.com",
            approver_user_id="@owner:example.com",
            timeout_seconds=60,
            thread_id="$thread",
        )
    finally:
        await store.close()

    assert receipt == "runtime-receipt"
    assert len(calls) == 1
    manager.request_approval.assert_awaited_once_with(
        tool_name="runtime.hermes.calendar.create",
        arguments={
            "action_id": "action-live",
            "arguments": {"title": "Review"},
            "runtime": "hermes",
        },
        room_id="!room:example.com",
        requester_id="@owner:example.com",
        approver_user_id="@owner:example.com",
        timeout_seconds=60,
        agent_name="runtime:hermes",
        thread_id="$thread",
        participant_id="hermes",
    )
