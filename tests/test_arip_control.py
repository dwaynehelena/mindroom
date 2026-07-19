"""Behavior tests for the live exact-payload ARIP approval ledger."""

# Test names document behavior and compact call formatting keeps scenarios readable.
# ruff: noqa: ANN001, ANN201, ANN202, COM812, D103

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from mindroom.arip_control import ApprovalControlError, ApprovalControlStore, executable_payload_digest

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path):
    value = ApprovalControlStore(tmp_path / "approval.db")
    await value.open()
    yield value
    await value.close()


async def _request(store: ApprovalControlStore, *, arguments=None, quorum: int = 2):
    return await store.request(
        approval_id="approval-1",
        tool_call_event_id="tool-1",
        tool_name="send_message",
        arguments=arguments or {"recipient": "alice", "body": "hello"},
        eligible_actors=("@alice:test", "@bob:test"),
        quorum=quorum,
        expires_at=NOW + timedelta(minutes=5),
    )


async def test_quorum_approval_consumes_only_exact_payload_once(store: ApprovalControlStore) -> None:
    arguments = {"recipient": "alice", "body": "hello", "token": "secret"}
    await _request(store, arguments=arguments)
    first = await store.decide(
        approval_id="approval-1", actor_id="@alice:test", decision="approved", decided_at=NOW
    )
    assert first.status == "pending"
    second = await store.decide(
        approval_id="approval-1", actor_id="@bob:test", decision="approved", decided_at=NOW
    )
    assert second.status == "approved"
    await store.consume(
        approval_id="approval-1", tool_name="send_message", arguments=arguments, observed_at=NOW
    )
    with pytest.raises(ApprovalControlError, match="already consumed"):
        await store.consume(
            approval_id="approval-1", tool_name="send_message", arguments=arguments, observed_at=NOW
        )


async def test_redacted_equivalent_payload_cannot_reuse_approval(store: ApprovalControlStore) -> None:
    original = {"recipient": "alice", "password": "first"}
    changed = {"recipient": "alice", "password": "second"}
    assert executable_payload_digest("send_message", original) != executable_payload_digest("send_message", changed)
    await _request(store, arguments=original, quorum=1)
    await store.decide(
        approval_id="approval-1", actor_id="@alice:test", decision="approved", decided_at=NOW
    )
    with pytest.raises(ApprovalControlError, match="does not match"):
        await store.consume(
            approval_id="approval-1", tool_name="send_message", arguments=changed, observed_at=NOW
        )


async def test_denial_expiry_ineligible_actor_and_equivocation_fail_closed(store: ApprovalControlStore) -> None:
    await _request(store, quorum=1)
    with pytest.raises(ApprovalControlError, match="not eligible"):
        await store.decide(
            approval_id="approval-1", actor_id="@mallory:test", decision="approved", decided_at=NOW
        )
    denied = await store.decide(
        approval_id="approval-1", actor_id="@alice:test", decision="denied", decided_at=NOW
    )
    assert denied.status == "denied"
    with pytest.raises(ApprovalControlError, match="equivocation"):
        await store.decide(
            approval_id="approval-1", actor_id="@alice:test", decision="approved", decided_at=NOW
        )
    with pytest.raises(ApprovalControlError, match="not executable"):
        await store.consume(
            approval_id="approval-1",
            tool_name="send_message",
            arguments={"recipient": "alice", "body": "hello"},
            observed_at=NOW + timedelta(minutes=6),
        )


async def test_concurrent_requests_are_transactionally_serialized(store: ApprovalControlStore) -> None:
    async def create(index: int) -> None:
        await store.request(
            approval_id=f"approval-{index}",
            tool_call_event_id=f"tool-{index}",
            tool_name="read_file",
            arguments={"path": f"file-{index}.txt"},
            eligible_actors=("@alice:test",),
            quorum=1,
            expires_at=NOW + timedelta(minutes=5),
        )

    await asyncio.gather(*(create(index) for index in range(20)))
