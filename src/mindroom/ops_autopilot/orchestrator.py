"""End-to-end orchestrator for the Personal Ops Autopilot pipeline.

Pipeline: collect -> compose -> gate -> deliver -> report.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from mindroom.ops_autopilot.approval.gate import ApprovalGate, ApprovalOutcome
from mindroom.ops_autopilot.collectors.registry import CollectorRegistry, build_default_registry
from mindroom.ops_autopilot.composer import compose_brief
from mindroom.ops_autopilot.delivery.telegram import DeliveryReceipt, TelegramDeliverer


@dataclass(slots=True)
class PipelineReport:
    """Full result of one pipeline run."""

    brief: str = ""
    approval: ApprovalOutcome | None = None
    receipt: DeliveryReceipt | None = None
    delivered_event_id: str | None = None
    collect_errors: list[str] = field(default_factory=list)
    ok: bool = False

    def summary(self) -> str:
        lines = [
            "📋 Ops Autopilot pipeline report",
            f"  • collect: {len(self.collect_errors)} error(s)",
            f"  • approval: {self.approval.status if self.approval else 'skipped'}",
        ]
        if self.receipt is not None:
            lines.append(
                f"  • delivery: {'delivered' if self.receipt.ok else 'failed'} "
                f"event_id={self.receipt.event_id or 'n/a'}"
            )
        if self.collect_errors:
            lines.append("  • errors:")
            lines.extend(f"    - {e}" for e in self.collect_errors)
        lines.append(f"  • overall: {'OK' if self.ok else 'FAILED'}")
        return "\n".join(lines)


class OpsAutopilotOrchestrator:
    """Coordinate collectors, composer, approval gate, and delivery."""

    def __init__(
        self,
        *,
        registry: CollectorRegistry | None = None,
        deliverer: TelegramDeliverer | None = None,
        gate: ApprovalGate | None = None,
        room_id: str | None = None,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._deliverer = deliverer or TelegramDeliverer()
        self._gate = gate or ApprovalGate()
        self._room_id = room_id

    async def run_async(self) -> PipelineReport:
        """Run the full pipeline asynchronously and return the report."""
        results = self._registry.run_all()
        report = PipelineReport()
        report.collect_errors = [
            f"{r.source}: {r.error}" for r in results if not r.ok and r.error
        ]
        report.brief = compose_brief(results)

        report.approval = await self._gate.gate(report.brief, room_id=self._room_id)
        if not report.approval.approved:
            report.ok = False
            return report

        report.receipt = self._deliverer.deliver(report.brief)
        if report.receipt.ok:
            report.delivered_event_id = report.receipt.event_id
            report.ok = True
        return report

    def run(self) -> PipelineReport:
        """Synchronous entry point wrapping :meth:`run_async`."""
        return asyncio.run(self.run_async())