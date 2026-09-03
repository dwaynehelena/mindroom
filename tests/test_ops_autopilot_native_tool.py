"""Unit tests for the ops-autopilot in-process native tool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mindroom.ops_autopilot.native_tool import OpsAutopilotNativeTools


def _context(*, room: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        room=room,
        room_id="!r:localhost",
        client=MagicMock(),
    )


@pytest.mark.asyncio
async def test_run_autopilot_brief_unavailable_without_room() -> None:
    tools = OpsAutopilotNativeTools()
    with patch(
        "mindroom.ops_autopilot.native_tool.get_tool_runtime_context",
        return_value=_context(room=None),
    ):
        result = await tools.run_autopilot_brief()
    assert "unavailable" in result


@pytest.mark.asyncio
async def test_run_autopilot_brief_refuses_without_live_store() -> None:
    tools = OpsAutopilotNativeTools()
    with (
        patch(
            "mindroom.ops_autopilot.native_tool.get_tool_runtime_context",
            return_value=_context(room=object()),
        ),
        patch("mindroom.ops_autopilot.native_tool.get_approval_store", return_value=None),
    ):
        result = await tools.run_autopilot_brief()
    assert "No live approval store" in result


@pytest.mark.asyncio
async def test_run_autopilot_brief_runs_orchestrator_in_process() -> None:
    tools = OpsAutopilotNativeTools()
    report = MagicMock()
    report.summary.return_value = "📋 Ops Autopilot pipeline report"
    orch = MagicMock()
    orch.run_async = AsyncMock(return_value=report)

    with (
        patch(
            "mindroom.ops_autopilot.native_tool.get_tool_runtime_context",
            return_value=_context(room=object()),
        ),
        patch("mindroom.ops_autopilot.native_tool.get_approval_store", return_value=object()),
        patch(
            "mindroom.ops_autopilot.native_tool.OpsAutopilotOrchestrator",
            return_value=orch,
        ) as mock_orch,
    ):
        result = await tools.run_autopilot_brief()

    assert result == "📋 Ops Autopilot pipeline report"
    orch.run_async.assert_awaited_once()
    # The orchestrator is constructed with the live room id.
    assert mock_orch.call_args.kwargs["room_id"] == "!r:localhost"