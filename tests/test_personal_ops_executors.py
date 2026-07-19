"""Tests for concrete Personal Ops write boundaries."""

# ruff: noqa: ANN001, ANN202, D103, S105

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from mindroom.personal_ops import OpsAction, PersonalOpsError
from mindroom.personal_ops_executors import MindRoomActionExecutor, OpenClawGatewayExecutor

NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _action(runtime="mindroom", tool_name="gmail.send", arguments=None):
    return OpsAction(
        "action-1",
        "mail",
        tool_name,
        arguments or {"to": "person@example.com", "body": "private"},
        runtime,
        "approval-1",
        NOW,
    )


@pytest.mark.asyncio
async def test_mindroom_executor_invokes_only_exact_allowlisted_function() -> None:
    calls = []

    async def invoke(tool_name, function_name, arguments):
        calls.append((tool_name, function_name, arguments))
        return {"message_id": "42"}

    executor = MindRoomActionExecutor(invoke, frozenset({"gmail.send"}))
    receipt = await executor(_action(), "stable-key")

    assert calls == [("gmail", "send", {"to": "person@example.com", "body": "private"})]
    assert receipt.startswith("mindroom:stable-key:")
    with pytest.raises(PersonalOpsError, match="not allowlisted"):
        await executor(_action(tool_name="gmail.delete"), "stable-key")


@pytest.mark.asyncio
async def test_openclaw_executor_uses_stdin_and_propagates_idempotency(tmp_path, monkeypatch) -> None:
    capture = tmp_path / "capture.json"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json,sys\n"
        "request=json.loads(sys.stdin.readline())\n"
        f"json.dump(request,open({str(capture)!r},'w'))\n"
        "print(json.dumps({'ok':True,'result':{'messageId':'42'}}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_OPENCLAW_TOKEN", "private-token")
    executor = OpenClawGatewayExecutor(
        "ws://127.0.0.1:18789",
        "TEST_OPENCLAW_TOKEN",
        frozenset({"send"}),
        5,
        worker,
        node_path="python3",
    )

    receipt = await executor(_action(runtime="openclaw", tool_name="send"), "stable-key")
    request = json.loads(capture.read_text(encoding="utf-8"))

    assert request["token"] == "private-token"
    assert request["params"]["idempotencyKey"] == "stable-key"
    assert receipt.startswith("openclaw:stable-key:")


@pytest.mark.asyncio
async def test_openclaw_executor_fails_closed_on_key_equivocation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_OPENCLAW_TOKEN", "private-token")
    executor = OpenClawGatewayExecutor(
        "ws://127.0.0.1:18789",
        "TEST_OPENCLAW_TOKEN",
        frozenset({"send"}),
        5,
        tmp_path / "unused",
    )
    with pytest.raises(PersonalOpsError, match="equivocation"):
        await executor(
            _action(runtime="openclaw", tool_name="send", arguments={"idempotencyKey": "different"}),
            "stable-key",
        )
