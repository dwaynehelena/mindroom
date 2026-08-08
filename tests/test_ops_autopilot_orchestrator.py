"""Unit tests for the ops-autopilot orchestrator (pipeline end-to-end)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mindroom.ops_autopilot.approval.gate import ApprovalOutcome
from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult
from mindroom.ops_autopilot.collectors.git import GitSummary
from mindroom.ops_autopilot.collectors.registry import CollectorRegistry
from mindroom.ops_autopilot.delivery.telegram import DeliveryReceipt
from mindroom.ops_autopilot.orchestrator import OpsAutopilotOrchestrator, PipelineReport


class _GitCollector(BaseCollector):
    name = "git"

    def collect(self) -> CollectResult:
        return CollectResult("git", True, data=GitSummary(branch="main", recent_commits=["abc x"]))


def _registry() -> CollectorRegistry:
    return CollectorRegistry([_GitCollector()])


def _approve_gate(*, approved: bool = True):
    gate = MagicMock()
    outcome = ApprovalOutcome(approved=approved, status="approved" if approved else "denied")
    gate.gate = AsyncMock(return_value=outcome)
    return gate, outcome


def _deliverer(*, ok: bool = True, event_id: str = "$e"):
    deliverer = MagicMock()
    deliverer.deliver.return_value = DeliveryReceipt(event_id=event_id, delivered=ok) if ok else DeliveryReceipt(error="boom")
    return deliverer


@pytest.mark.asyncio
async def test_run_async_delivers_when_approved() -> None:
    gate, outcome = _approve_gate(approved=True)
    deliverer = _deliverer(ok=True)
    orch = OpsAutopilotOrchestrator(registry=_registry(), gate=gate, deliverer=deliverer)
    report = await orch.run_async()

    assert report.approval is outcome
    assert report.ok is True
    assert report.delivered_event_id == "$e"
    assert report.collect_errors == []
    deliverer.deliver.assert_called_once()
    gate.gate.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_async_blocks_delivery_on_deny() -> None:
    gate, outcome = _approve_gate(approved=False)
    deliverer = _deliverer(ok=True)
    orch = OpsAutopilotOrchestrator(registry=_registry(), gate=gate, deliverer=deliverer)
    report = await orch.run_async()

    assert report.approval is outcome
    assert report.approval.approved is False
    assert report.ok is False
    assert report.delivered_event_id is None
    # The executor must NOT run when denied.
    deliverer.deliver.assert_not_called()


@pytest.mark.asyncio
async def test_run_async_collect_errors_recorded() -> None:
    class _Bad(BaseCollector):
        name = "bad"

        def collect(self) -> CollectResult:
            return CollectResult("bad", False, error="exploded")

    registry = CollectorRegistry([_GitCollector(), _Bad()])
    gate, _ = _approve_gate(approved=True)
    deliverer = _deliverer(ok=True)
    orch = OpsAutopilotOrchestrator(registry=registry, gate=gate, deliverer=deliverer)
    report = await orch.run_async()
    assert report.collect_errors == ["bad: exploded"]


@pytest.mark.asyncio
async def test_run_async_delivery_failure_marks_not_ok() -> None:
    gate, _ = _approve_gate(approved=True)
    deliverer = _deliverer(ok=False)
    orch = OpsAutopilotOrchestrator(registry=_registry(), gate=gate, deliverer=deliverer)
    report = await orch.run_async()
    assert report.ok is False
    assert report.delivered_event_id is None


def test_run_sync_wraps_async() -> None:
    orch = OpsAutopilotOrchestrator(
        registry=_registry(),
        gate=_approve_gate(approved=True)[0],
        deliverer=_deliverer(ok=True),
    )
    report = orch.run()
    assert isinstance(report, PipelineReport)
    assert report.ok is True


def test_pipeline_report_summary() -> None:
    report = PipelineReport(brief="b", approval=ApprovalOutcome(False, "denied"), ok=False)
    text = report.summary()
    assert "approval: denied" in text
    assert "overall: FAILED" in text